from __future__ import annotations
from app.creation.task_input_reads import creation_task_from_payload
from app.creation.media_result_use_case import MediaResultUseCase, MediaResultRejected
from app.assistant.media_result_binding import AssistantMediaResultBinding
from app.assistant.execution_binding import AssistantExecutionBinding, ExecutionBindingRejected
from app.assistant import home_plan_turn_use_case as home_plan
from app.assistant.stream_response import AssistantStreamTransport
from app.assistant.task_turn_routes import TaskTurnHTTPAdapter, build_task_turn_router
from app.assistant.conversation_routes import ConversationHTTPAdapter, build_conversation_router
from app.materials.library_routes import MaterialLibraryHTTPAdapter, build_material_library_router
from app.materials.evidence_routes import build_material_evidence_router
from app.materials.intake_routes import MaterialIntakeHTTPAdapter, build_material_intake_router
from app.materials.catalog_routes import MaterialCatalogHTTPAdapter, build_material_catalog_router
from app.materials.link_routes import MaterialLinkHTTPAdapter, build_material_link_router
from app.materials.link_intake import MaterialLinkProvenanceSink
from app.creation.video_template_routes import VideoTemplateHTTPAdapter, build_video_template_router
from app.assistant.task_turn_scope_use_case import TaskTurnScopeUseCase, TaskTurnSelection, TaskTurnScopeRejected
from app.creation.editing.edit_capability_read_use_case import EditCapabilityReadUseCase
from app.assistant.storyboard_result_binding import (AssistantStoryboardResultBinding,
    StoryboardResultRejected, UnknownStoryboardReference)
from app.assistant.edit_result_binding import AssistantEditResultBinding, EditResultRejected
from app.assistant.script_tool_result_binding import (AssistantScriptToolResultBinding,
    ScriptToolReceiptRejected, ScriptToolLookupRejected, tool_metadata)
from app.assistant.task_business import TaskBusiness
from app.assistant.task_business_errors import TaskBusinessRejected
from app.assistant.goal_readiness_use_case import GoalReadinessUseCase, GoalEvidenceUnavailable
from app.creation.source_errors import SourcePreflightError as _CreationTaskTopicPreflightError
from app.creation.script_generation_intent import ScriptGenerationIntent
from app.creation.script_generation_routes import build_script_generation_router
from app.creation.script_draft_use_case import ScriptDraftUseCase, ScriptDraftRejected
from app.creation.execution_control import CreationExecutionControl, DesktopShutdownUseCase, ExecutionControlRejected
from app.creation.execution_routes import build_creation_execution_router
from app.creation.render_stage_use_case import RenderStageUseCase, RenderRejected
from app.creation.montage_stage_use_case import MontageStageUseCase, MontageStageRejected
from app.creation.person_stage_use_case import PersonStageUseCase,PersonStageRejected
from app.creation.narration_stage_use_case import NarrationStageUseCase
from app.creation.narration_artifacts import NarrationArtifactRecord
from app.creation.person_renders import PersonRender

import hashlib
import hmac
import ipaddress
from functools import cache
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote
from uuid import UUID, uuid4
from weakref import WeakSet

if TYPE_CHECKING:
    from app.device_runtime import DeviceRuntime

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

import httpx

from app.assistant_pi import (
    ASSISTANT_BUSINESS_TOOLS, AssistantStopped as AssistantStopped, AssistantUnavailable as AssistantUnavailable, forget_turn_control,
    pi_runtime_checks, register_turn_control,
    request_stop_turn, run_material_turn as run_material_turn, run_plan_turn, run_task_turn,
    stop_all_assistant_turns,
)
from app.assistant.projection import _safe_business_failure, _safe_compaction_event
from app.assistant_goal_continuation import GoalContinuationService, GoalResultProjection, GoalProjectionRejected

from app.compound_edits import compile_compound_edit as compile_compound_edit
from app.graphic_sources import import_graphic_source, graphic_sources, resolve_graphic_source, graphic_source_facts
from app.visual_effects import clip_playback_rate as clip_playback_rate
from app.subtitle_edits import normalize_subtitle_text_and_breaks
from app.profiles.contracts import (Authorization, PersonProfile, VoiceProfile as VersionedVoiceProfile, profile_use_failure)
from app.auth import (
    AuthDependencyUnavailableError,
    ForbiddenError,
    SupabaseAuthContext,
    UnauthorizedError,
)
from app.device_accounts import DeviceAccountError, DeviceAccountService, DeviceCredential, report_creation_identity_unknown
from app.auth import WORKSPACE_SELECTION_HEADER
from app.companion.identity import CompanionIdentity, require_account_principal, require_companion_identity
from app.companion.routes import build_companion_router, companion_http_error
from app.companion.pairing import CompanionPairingService
from app.mobile_transfer_routes import build_mobile_transfer_router
from app.mobile_transfers import MobileTransferActor
from app.artifact_store import ArtifactStore as ArtifactStore
from app.bootstrap import DatabaseOwners, RuntimeDependencies, prepare_runtime_dependencies, bind_device_runtime
from app.creation.audio_ingress_use_case import AudioIngressUseCase, AudioIngressRejected, AUDIO_INGRESS_ACTIONS
from app.audio_timeline import AudioEditRequest, narration_audio_timeline as narration_audio_timeline
from app.task_audio import TaskAudio, TaskMediaJobs, TASK_MEDIA_CONTEXT, TASK_MEDIA_KIND, TaskMediaRequired, media_digest
from app.creation.clip_preview import ClipPreviewCommand, ClipPreviewUseCase, ClipPreviewUnavailable, needs_full_visual_preview
from app.media.clip_preview import ClipPreviewExecutor
from app.media.picture_preview import PicturePreviewRenderer, PicturePreviewRejected
from app.creation.visual_preview_use_case import VisualPreviewUseCase
from app.creation.editing.clips import ClipPreviewConflict
from app.creation.editing.audio_edit_use_case import AudioEditUseCase, AudioEditRejected
from app.creation.output_observation_use_case import OutputObservationUseCase, OutputObservationRejected
from app.creation.editing.timeline_history import TimelineHistoryConflict
from app.creation.editing.timeline_restore_use_case import (TimelineRestoreUseCase, TimelineRestoreCommand,
    TimelineRestoreUnavailable)
from app.creation.editing.linked_timeline_use_case import (LinkedTimelineEditUseCase, LINKED_EDIT_OPERATIONS)
from app.creation.editing.metadata_use_case import (
    MetadataEditUseCase, MetadataEditReplay, InvalidMontage, LegacyMontageReference, EditPreviewRejected,
    metadata_edit_command as metadata_edit_command,
    default_subtitle_style as metadata_default_subtitle_style,
    execution_subtitle_track as metadata_execution_subtitle_track,
    narration_linkage_spans as metadata_narration_linkage_spans,
    refresh_montage_review_state as metadata_refresh_montage_review_state,
    prepare_changed_montage as metadata_prepare_changed_montage,
    logical_montage_output_refs as metadata_logical_montage_output_refs,
)
from app.creation.editing.compound_use_case import CompoundEditUseCase
from app.creation.editing.subtitle_use_case import (SubtitleEditUseCase, is_subtitle_edit, subtitle_values)
from app.creation.editing.exact_preview_use_case import ExactPreviewUseCase, validate_edit_origin, project_submitted_edit_input
from app.creation.editing.command_preview_use_case import CommandPreviewUseCase
from app.creation.editing.command_compile import AudioCommandPreview,CommandParseRejected
from app.creation.editing.narration_preview_use_case import NarrationPreviewUseCase
from app.creation.editing.person_preview_use_case import PersonPreviewUseCase
from app.creation.projection import project_task_summary
from app.creation_task_responses import CreationTaskResponseAdapter
from app.creation_task_text_responses import CreationTaskTextResponseAdapter
from app.task_edit_confirmation import TaskEditConfirmationHttpAdapter, MetadataEditConfirmationHttpAdapter
from app.task_edit_commands import TaskEditCommandHttpAdapter
from app.content_identity_http import ContentIdentityHttpAdapter
from app.llm_route_snapshot import WorkspaceLlmRouteSnapshot
import app.creation.transactions as _owner_app_creation_transactions
from app.creation import media_script_submission_use_case as script_submission
from app.media_script import MediaScriptError
from app.media_script_jobs import read_script_toolchain, MediaScriptPreviewUseCase, MEDIA_SCRIPT_ACTIONS
from app.media_script_tools import MediaScriptTools, ScriptToolError, ScriptToolConflict, ScriptToolUnknown
from app.visual_effects import with_visual_effects as with_visual_effects
from app.local_portrait import LocalPortrait
from app.local_members import LocalMember, LocalMembersError
from app.local_member_routes import build_local_member_router
from app.profiles.authorization_routes import build_authorization_router
from app.profiles.pronunciation_routes import build_pronunciation_router
from app.profiles.profile_routes import build_profile_router
from app.profiles.preview_routes import build_preview_router
from app.device_scheduler import SessionHeartbeat, RecoveryBlocked
from app.member_permits import (
    HostBudgetUncalibrated,
    creation_job_registration, find_member, permit_actor, resolve_member_token,
)
from app.device_resource_configuration import resolve_device_resources
from app.device_resource_profile import DeviceProfileUnavailable
from app.device_runtime import RuntimeUnavailable


import app.visual_understanding as _owner_app_visual_understanding
from app.creation.cover_use_case import CoverUseCase, COVER_JOB_KIND, COVER_OPERATION
from app.cover_generation import (
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
    presenter_input_facts as presenter_input_facts,
    CpuRenderGrantContext, CreationStageGrantContext, CreationExecution, CreationExecutionActor,
    CreationJobUnitExecutor,
    revision_digest as revision_digest, submit_creation_stage as submit_creation_stage, PERSON_EDIT_KIND, NARRATION_EDIT_KIND,
)
from app.creator_profile import get_creator_profile
from app.creator_profile_routes import build_creator_profile_router
from app.drafts import DraftWorkspace as DraftWorkspace
from app.edit_plan import (
    build_edit_plan_workspace,
    safe_processed_video_path,
)
from app.external_sources import HtmlPageSourceClient
from app.hermes_radar import HermesRadarSourceClient
from app.model_installer import ModelInstaller
from app.model_install_routes import build_model_install_router
from app.application_info_routes import build_application_info_router
from app.voice_reference_routes import build_voice_reference_router
from app.media_capability_routes import build_media_capability_router
from app.voice_transcription_routes import build_voice_transcription_router
from app.task_visual_routes import ClipPreviewSubmissionHttpAdapter, build_task_visual_router
from app.task_audio_routes import build_task_audio_router
from app.production_profile_routes import build_production_profile_router
from app.legacy_voice_routes import build_legacy_voice_router
from app.media_script_tool_routes import build_media_script_tool_router
from app.video_intake_routes import build_video_intake_router
from app.device_session_routes import build_device_session_router
from app.task_media_routes import build_task_media_router
from app.runtime_settings_routes import build_runtime_settings_router
from app.model_runtime import QWEN3_TTS_06B_BASE_8BIT
from app.models import EpisodeStatus, FeedbackEvent, ScriptPackage, ScriptSection, VideoJobStatus, utc_now
from app.montage import (
    CreationTaskMontagePlan,
    MontageClip as MontageClip,
    creation_task_montage_plan_from_payload,
    creation_task_montage_plan_to_payload,
    _validate_rendered_media as _validate_rendered_media,
)
from app.preference_routes import build_preference_router
from app.preflight import setup_preflight, workspace_preflight
from app.profiles.voice_sampling_routes import build_voice_sampling_router
from app.assistant.references import AssistantReferenceConflict, normalize_selection, validate_timeline_reference
from app.product_hunt import ProductHuntClient
from app.scheduled_task_routes import build_scheduled_task_router
from app.materials.contracts import MediaAuthorityError
from app.production import EXACT_EDIT_SOURCE, TimelineVersionConflict, ProductionProfile, ProductionRepository, material_segment_eligible
from app.creation.task_workspace_read_use_case import TaskWorkspaceReadUseCase
from app.creation.plan_contracts import CreationPlan
from app.source_policy import knowledge_item_readable as content_readable
from app.creation.contracts import CreationTask, NarrationSegment
from app.creation.script_contracts import CreationTaskScriptSection
from app.creation.publish_output_qualification_use_case import PublishOutputQualificationUseCase
from app.creation.video_template_use_case import VideoTemplateUseCase
from app.creation import request_workflow_use_case as request_workflow
from app.creation.plan_preflight_use_case import (CreationPlanPreflightInput,
    CreationPlanPreflightUseCase)
from app.source_types import source_item_identity as source_item_identity
from app.creation.contracts import (
    CoverAsset,
    EditIntent,
    EditProposal,
    TimelineVersion,
    SubtitleTrack,
)
from app.quality_inspection import QualityCheckRepository as QualityCheckRepository
from app.creation.quality_inspection_use_case import QualityInspectionUseCase, QUALITY_KIND
from app.creation.quality_execution import quality_check_registration
from app.schemas import CreationTaskObservationRequest, CreationTaskObservationTarget
from app.subtitle_tracks import (
    SubtitleTrack as SubtitleExecutionTrack,
    render_bilingual_srt,
    render_srt,
    subtitle_translation_provider_from_settings,
)
from app.voice_clone import VoiceCloneError
from app.repository import (
    LEGACY_WORKSPACE_ID,
    active_workspace_id,
    reset_workspace_context,
    set_auth_enabled,
    set_workspace_context,
)
from app.runtime_health import runtime_health, model_catalog_from_settings
from app.runtime_settings import (
    apply_runtime_settings_save,
    device_runtime_settings_values,
    is_local_client_host,
    runtime_settings_updates,
)
from app.workspace_settings import (
    workspace_settings,
)
from app.source_connector_routes import build_source_connector_router
from app.knowledge_routes import build_knowledge_router
from app.source_item_routes import build_source_item_router
from app.episode_read_routes import build_episode_read_router
from app.home_aggregation import aggregate_home
from app.schemas import ScriptLibrarySourceResponse as ScriptLibrarySourceResponse, AuthorizationResponse, AuthorizationUpsertRequest, ContentTemplatesResponse, CreationPlanPreviewRequest, CreationPlanPreviewResponse, CreationRequestWindowPreviewRequest, CreationRequestResponse, CreationRequestItemsResponse, CreationTaskScriptConfirmRequest as CreationTaskScriptConfirmRequest, CreationTaskScriptDraftRequest as CreationTaskScriptDraftRequest, TaskKnowledgeAdoptionResponse, CreationTaskScriptResponse, CreationTaskNarrationVoiceSelectionResponse, CreationTaskPersonRendersResponse, CreationTaskMontagePlanResponse, AssistantStoryboardRegenerationReceipt, AssistantStoryboardRegenerationFailure, CreationTaskQualityCheckResponse, CreationTaskEditConfirmResponse, CreationTaskEditPreviewRequest, CreationTaskAssistantTurnRequest, CreationTaskAssistantHistoryResponse, MaterialAssistantTurnResponse, MaterialAssistantResultResponse, WorkspaceAssistantPlanTurnRequest, WorkspaceAssistantPlanTurnResponse, CreationTaskEditPreviewResponse, CreationTaskExactEditRequest, AssistantScriptToolReceipt, MediaScriptPreviewRequest, TaskMediaJobResponse, MediaScriptToolSaveRequest, MediaScriptToolUpdateRequest, MediaScriptToolRevokeRequest, MediaScriptToolPreviewRequest, EXACT_EDIT_FIELDS, EXACT_EDIT_ENVELOPE_FIELDS, CreationTasksResponse, CreatorProfileResponse, DraftAssetResponse, DraftEnvironmentResponse, DraftStatusResponse, EpisodeVoiceSelectionResponse, KnowledgeNoteResponse, MaterialAssetResponse, MaterialLibraryInsightsResponse, MaterialLibraryResponse, MediaCapabilityResponse, MontageClipCandidateResponse, MontagePlansResponse, PendingVideoResponse, ProcessedVideoResponse, ProductionBatchesResponse, ProductionFoundationResponse, ProductionProfileResponse, PersonProfileResponse, SourceConnectorResponse, SourceRunEvidenceResponse, SystemStatusResponse, VoiceProfileResponse, VoiceReferenceImportResponse, VoiceProfilesResponse, VersionedVoiceProfileResponse, VoiceTranscriptionResponse, WorkflowStatusResponse, CoverAssetResponse, SubtitleTrackResponse, TimelineVersionArtifactResponse, TimelineVersionDetailResponse, TimelineVersionListResponse, TimelineVersionNarrationSegmentResponse, TimelineVersionShotResponse, TimelineVersionSummaryResponse
from app.knowledge_files import parse_knowledge_file
from app.knowledge_grant_routes import knowledge_grant_router
from app.knowledge_grants import VerifiedMemberGrantTargetResolver
import app.platform_auth.service as _owner_app_platform_auth_service
from app.platform_publish import (
    build_platform_publish_router,
    platform_publish_registration,
    recover_platform_publish_jobs,
)
from app.sources import ObsidianVault as ObsidianVault
from app.source_types import SourceConnector, SourceItem
from app.source_sync import SourceBoundaryError, SourceSyncFailedError, sync_source_connector

from app.topics import (
    CreationTaskScriptGenerationError,
    OpenAICompatibleCreationTaskTopicGenerator,
    OpenAICompatibleCreationTaskScriptGenerator,
    topic_sources_configured,
)
from app.voice import transcribe_audio_bytes
from app.automation_routes import build_automation_guarded_publish_router
from contextlib import contextmanager
from app.automation_routes import build_automation_router
from app.automation_runtime import AutomationRuntime

from app.desktop_shutdown_routes import build_desktop_shutdown_router
from app.device_invite_accept_routes import (
    DeviceInviteAcceptResponse, build_device_invite_accept_router,
)
from app.content_template_routes import build_content_template_router
from app.editing_resource_catalog import editing_resource_catalog
from app.editing_resource_routes import build_editing_resource_router
from app.creation.source_inputs_use_case import CreationSourceInputs
from app.task_edit_routes import CoverConfirmationHttpAdapter, TaskEditReceiptHttpAdapter, build_task_edit_router
from app.task_observation_routes import TaskObservationResponseAdapter, build_task_observation_router
from app.task_media_script_routes import (
    MediaScriptSubmissionHttpAdapter, build_task_media_script_router,
    media_script_submission_source, media_script_submission_command,
)
from app.assistant_reference_routes import build_assistant_reference_router
from app.assistant_capability_routes import build_assistant_capability_router
from app.workspace_assistant_turn_routes import build_workspace_assistant_turn_router
from app.assistant.home_plan_http_adapter import HomePlanTurnHTTPAdapter
from app.creation_request_routes import build_creation_request_router
from app.creation_plan_routes import CreationPlanPreflightHttpAdapter, build_creation_plan_router
from app.source_task_routes import build_source_task_router
from app.creation.source_task_use_case import SourceTaskUseCase
from app.creation.script_library_routes import build_script_library_router
from app.creation.script_library_use_case import ScriptLibraryUseCase
from app.task_workspace_routes import TaskWorkspaceResponseAdapter, build_task_workspace_router
from app.task_topic_routes import build_task_topic_router
from app.task_knowledge_adoption_routes import build_task_knowledge_adoption_router
from app.creation.script_context_use_case import ScriptContextUseCase
from app.task_script_routes import build_task_script_router
from app.task_assistant_history_routes import build_task_assistant_history_router
from app.creation.script_routes import ScriptWindowHttpAdapter, build_script_sections_router
from app.task_narration_routes import build_task_narration_router
from app.task_person_render_routes import build_task_person_render_router
from app.storyboard_regeneration_routes import build_storyboard_regeneration_router
from app.task_montage_control_routes import build_task_montage_control_router
from app.native_task_read_routes import build_native_task_read_router
from app.assistant_semantic_receipt_routes import build_assistant_semantic_receipt_router
from app.task_segment_routes import build_task_segment_router
from app.task_budget_routes import build_task_budget_router
from app.task_capability_routes import TaskEditCapabilityResponseAdapter, build_task_capability_router
from app.timeline_restore_routes import build_timeline_restore_router
from app.creation.quality_routes import build_quality_router
from app.creation.quality_application import QualityApplication
from app.task_output_routes import build_task_output_router
from app.timeline_history_routes import build_timeline_history_router
from app.home_routes import build_home_router
from app.material_insights_routes import build_material_insights_router
from app.material_processing_routes import build_material_processing_router
from app.reference_video_routes import build_reference_video_router
from app.legacy_montage_routes import LegacyMontageResponseAdapter, build_legacy_montage_router
from app.knowledge_notes_routes import build_knowledge_notes_router
from app.device_preparation_routes import build_device_preparation_router
from app.device_resource_status import resource_preparation_status, preparation_actor_scope
_clone_subtitle_track = _owner_app_creation_transactions.clone_subtitle_track
LocalVisualUnderstanding = _owner_app_visual_understanding.LocalVisualUnderstanding
PlatformAuthService = _owner_app_platform_auth_service.PlatformAuthService

_RESOURCE_BUDGET_UNSET = object()


def _device_resource_profile():
    # A running driver keeps its selected revision; existing grants remain sealed.
    active = getattr(device_runtime, "resource_profile", None)
    if active is not None:
        return active
    try:
        return resolve_device_resources(settings, os.environ, model_catalog=model_catalog,
                                        select_packet_copy_preparation=True,
                                        select_audio_delivery_preparation=True,
                                        select_quality_preparation=True)
    except DeviceProfileUnavailable as exc:
        raise HostBudgetUncalibrated(str(exc)) from exc


def _device_host_budget():
    return _device_resource_profile().budget


def _device_operation_budget(name):
    try:
        return getattr(_device_host_budget(), name)
    except HostBudgetUncalibrated:
        # Missing media configuration must not disable metadata-only reads.
        return None

app = FastAPI(title="AI视频日记 Agent Studio API")
logger = logging.getLogger(__name__)
app.state.creation_permit_client = None
app.state.auth_http_client = None
app.state.desktop_control_token = os.environ.pop("YAOHE_DESKTOP_CONTROL_TOKEN", "")
app.state.desktop_shutdown = None
_desktop_shutdown_lock = threading.Lock()
DESKTOP_SHUTDOWN_PATH = "/internal/desktop-shutdown"



app.include_router(build_desktop_shutdown_router(
    shutdown_path=DESKTOP_SHUTDOWN_PATH,
    app_for=lambda: app,
    ipaddress_for=lambda: ipaddress,
    regex_for=lambda: re,
    hmac_for=lambda: hmac,
    http_error_for=lambda: HTTPException,
    datetime_for=lambda: datetime,
    utc_for=lambda: UTC,
    time_for=lambda: time,
    lock_for=lambda: _desktop_shutdown_lock,
    shutdown_type_for=lambda: DesktopShutdownUseCase,
    runtime_for=lambda: device_runtime,
    automation_for=lambda: automation_runtime,
    ingestion_for=lambda: material_ingestion,
    assistant_for=lambda: assistant_continuation_driver,
    stop_assistant_for=lambda: stop_all_assistant_turns,
    conversations_for=lambda: conversation_repo,
    stop_auth_for=lambda: stop_auth_transport,
    stop_permit_for=lambda: stop_creation_permit_transport,
))


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


@app.on_event("startup")
def start_auth_transport() -> None:
    if app.state.auth_http_client is None:
        app.state.auth_http_client = httpx.Client(timeout=10.0)
    auth_context.bind_http_client(app.state.auth_http_client)


def stop_auth_transport() -> None:
    client = app.state.auth_http_client
    if client is not None:
        client.close()
        auth_context.bind_http_client(None)
        app.state.auth_http_client = None


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
    conversation_repo.task_goal_input_authorized=lambda goal,*,connection: _task_authorizations_for_goal().adopted_goal_input_valid(goal,connection=connection)
    local_members_service, platform_auth_service = database.members, database.platform_auth
    scheduled_task_service, video_job_runner = database.scheduled_tasks, database.video_jobs
    model_catalog, visual_semantic_provider, voice_clone_provider = models.catalog, models.visual, models.voice
    avatar_lipsync_provider, latentsync_provider = models.avatar, models.latentsync
    montage_planner, montage_renderer, artifact_store = media.planner, media.renderer, media.artifacts
    narration_generator, voice_preview_service, avatar_preview_service = media.narration, media.voice_preview, media.avatar_preview
    video_inbox, video_processor, video_pipeline = media.inbox, media.processor, media.pipeline
    draft_workspace, source_tracker = media.drafts, media.source_tracker
    vault, service, workflow = content.vault, content.episodes, content.workflow
    candidate.auth.bind_http_client(app.state.auth_http_client)
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
    production_repo.person_renders.interrupt_running_person_renders()


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




app.include_router(build_model_install_router(
    catalog_for=lambda: model_catalog,
    installer_for=lambda: model_installer,
    is_local_host=lambda host: is_local_client_host(host),
))






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
    if path == DESKTOP_SHUTDOWN_PATH and request.method == "POST":
        return await call_next(request)
    if app.state.desktop_shutdown is not None and request.method not in {"GET", "HEAD", "OPTIONS"}:
        return JSONResponse(status_code=503, content={"detail": "应用正在保存并暂停，未接收新操作"})
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
    except AuthDependencyUnavailableError as exc:
        return JSONResponse(status_code=503,
            content={"error": "auth_dependency_unavailable", "detail": str(exc)})
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



app.include_router(build_device_invite_accept_router(
    service_type_for=lambda: DeviceAccountService,
    settings_for=lambda: settings,
    credential_for=lambda: trusted_device_credential,
    error_type_for=lambda: DeviceAccountError,
    error_status_for=lambda: DEVICE_ACCOUNT_ERROR_STATUS,
    json_response_for=lambda: JSONResponse,
    response_for=lambda: DeviceInviteAcceptResponse,
))


LOCAL_MEMBER_TOKEN_HEADER = "X-Local-Member-Token"


def _local_member_token(request: Request) -> str:
    return request.headers.get(LOCAL_MEMBER_TOKEN_HEADER, "").strip()


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


def _content_identity_http_adapter():
    return ContentIdentityHttpAdapter(
        local_token_for=lambda request: _local_member_token(request),
        resolve_member=lambda token: local_members_service.resolve_member(token),
        auth_enabled=lambda: auth_context.enabled,
    )






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
        if not _content_identity_http_adapter().visible(request, workspace_id, task.visibility, task.owner_user_id):
            raise HTTPException(status_code=404, detail="creation task not found")
        return
    if kind == "material" and scope_id in {"library", "browse"}:
        return
    if kind == "material" and scope_id.startswith("asset:"):
        asset_id = scope_id.removeprefix("asset:")
        if asset_id:
            _material_library_http_adapter().get_production_material(asset_id, request)
            return
    if kind == "material" and scope_id.startswith("segment:"):
        parts = scope_id.split(":", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            asset = _material_library_http_adapter().get_production_material(parts[1], request)
            if any(segment.id == parts[2] and segment.asset_id == asset.id for segment in asset.segments):
                return
            raise HTTPException(status_code=404, detail="所选素材片段不存在")
    raise HTTPException(status_code=422, detail="对话范围与当前可见对象不匹配。")




# 知识授权路由复用既有的身份与工作区解析，不新造第二套：
# - `workspace_for` 保持当前工作区解析，`content_reader` 使用
#   `ContentIdentityHttpAdapter.reader`，与列表、详情、搜索、助手共用同一判据；
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
        database.sources, workspace_for=current_workspace_id, content_reader=_content_identity_http_adapter().reader,
        target_resolver=VerifiedMemberGrantTargetResolver(local_mode=not auth.enabled),
    ))
    router.include_router(build_automation_guarded_publish_router(build_platform_publish_router(
        database.production, workspace_for=current_workspace_id, content_reader=_content_identity_http_adapter().reader,
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


app.include_router(build_local_member_router(
    service_for=lambda: local_members_service,
    write_guard=lambda request: _local_member_write_guard(request),
    token_for=lambda request: _local_member_token(request),
))


def _episode_read_http_router(route_names):
    return build_episode_read_router(
        episodes_for=lambda: repo,
        output_root_for=lambda: settings.video_output_path,
        edit_plan_for=lambda: build_edit_plan_workspace,
        route_names=route_names,
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


def _application_info_http_router(route_names):
    return build_application_info_router(
        system_type_for=lambda: SystemStatusResponse,
        settings_for=lambda: settings,
        workflow_type_for=lambda: WorkflowStatusResponse,
        workflow_for=lambda: workflow,
        process_for=lambda: subprocess,
        file_for=lambda: __file__,
        route_names=route_names,
    )


app.include_router(_application_info_http_router(("get_system_status",)))


app.include_router(_application_info_http_router(("get_workflow_status",)))


app.include_router(build_scheduled_task_router(
    service_for=lambda: scheduled_task_service,
    workspace_for=lambda request: current_workspace_id(request),
))



app.include_router(build_content_template_router(
    response_for=lambda: ContentTemplatesResponse,
    catalog_for=lambda: list_content_templates,
))


app.include_router(build_editing_resource_router(
    catalog_for=editing_resource_catalog,
))






def _video_template_http_adapter():
    return VideoTemplateHTTPAdapter(
        case_for=lambda: VideoTemplateUseCase(production_repo),
        records_for=lambda: production_repo.video_templates,
        workspace_for=current_workspace_id,
        reader_for=_content_identity_http_adapter().reader,
        write_guard=_local_member_write_guard,
        task_for=_creation_task_or_404,
        plan_for=_creation_plan_for_task_or_404,
        output_for=_creation_task_output_path,
        tracks_for=_current_subtitle_tracks,
    )


app.include_router(build_video_template_router(_video_template_http_adapter, ('set_video_template_visibility_route',)))


app.include_router(build_video_template_router(_video_template_http_adapter, ('list_video_templates',)))


app.include_router(build_video_template_router(_video_template_http_adapter, ('create_video_template_version', 'read_video_template', 'save_visual_template', 'visual_template_preview')))


app.include_router(build_video_template_router(_video_template_http_adapter, ('save_creation_task_template',)))


def _production_profile_http_router(route_names):
    return build_production_profile_router(
        records_for=lambda: production_repo,
        catalog_for=lambda: model_catalog,
        voice_spec_for=lambda: QWEN3_TTS_06B_BASE_8BIT,
        replace_for=lambda: replace,
        workspace_for=lambda: current_workspace_id,
        reader_for=lambda: _content_identity_http_adapter().reader,
        foundation_type_for=lambda: ProductionFoundationResponse,
        profile_response_type_for=lambda: ProductionProfileResponse,
        sources_configured_for=lambda: topic_sources_configured,
        settings_for=lambda: settings,
        vault_for=lambda: vault,
        batch_response_for=lambda: _legacy_montage_response_adapter().production_batch_response,
        montage_response_for=lambda: _legacy_montage_response_adapter().montage_plan_response,
        montages_for=lambda: montage_repo,
        episodes_for=lambda: repo,
        episode_status_for=lambda: EpisodeStatus,
        http_error_type_for=lambda: HTTPException,
        profile_type_for=lambda: ProductionProfile,
        route_names=route_names,
    )


app.include_router(_production_profile_http_router(("get_production_foundation",)))


app.include_router(_production_profile_http_router(("update_production_profile",)))


app.include_router(build_legacy_voice_router(
    records_for=lambda: production_repo,
    workspace_for=lambda: current_workspace_id,
    profiles_type_for=lambda: VoiceProfilesResponse,
    profile_type_for=lambda: VoiceProfileResponse,
    http_error_type_for=lambda: HTTPException,
    capability_for=lambda: _capability_unavailable,
    selection_type_for=lambda: EpisodeVoiceSelectionResponse,
    episodes_for=lambda: repo,
    authority_type_for=lambda: MediaAuthorityError,
    authority_error_for=lambda: _media_authority_http_error,
))














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
    user_id, diagnostic = _content_identity_http_adapter().reader(request, profile.workspace_id)
    current = production_repo.profile_records.get_person_profile(profile.workspace_id, profile.id) if isinstance(profile, PersonProfile) else production_repo.profile_records.get_versioned_voice_profile(profile.workspace_id, profile.id)
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


app.include_router(build_authorization_router(
    records_for=lambda: production_repo,
    workspace_for=lambda request: current_workspace_id(request),
    authorization_for=lambda workspace_id, payload: _authorization_from_request(workspace_id, payload),
    response_for=lambda authorization: _authorization_response(authorization),
    write_error_for=lambda exc: _media_write_error(exc),
))




def _profile_http_router(route_names):
    return build_profile_router(
        records_for=lambda: production_repo,
        workspace_for=lambda request: current_workspace_id(request),
        reader_for=lambda request, workspace_id: _content_identity_http_adapter().reader(request, workspace_id),
        voice_response_for=lambda voice, request: _versioned_voice_profile_response(voice, request),
        person_response_for=lambda person, request: _person_profile_response(person, request),
        read_facts_for=lambda profile, request: _profile_read_facts(profile, request),
        required_text_for=lambda value, field: _required_media_text(value, field),
        optional_texts_for=lambda values, field: _optional_media_text_values(values, field),
        authorization_for=lambda workspace_id, payload: _authorization_from_request(workspace_id, payload),
        write_error_for=lambda exc: _media_write_error(exc),
        route_names=route_names,
    )


app.include_router(_profile_http_router(("get_default_media_voice",)))


app.include_router(_profile_http_router(("set_default_media_voice",)))


app.include_router(_profile_http_router(("stream_profile_reference",)))


app.include_router(_profile_http_router(("list_media_person_profiles",)))




app.include_router(_profile_http_router(("save_media_person_profile",)))


app.include_router(_profile_http_router(("get_media_person_profile",)))


app.include_router(_profile_http_router(("list_media_person_profile_versions",)))


app.include_router(build_voice_reference_router(
    path_type_for=lambda: Path,
    http_error_type_for=lambda: HTTPException,
    records_for=lambda: production_repo,
    workspace_for=lambda: current_workspace_id,
    reader_for=lambda: _content_identity_http_adapter().reader,
    response_type_for=lambda: VoiceReferenceImportResponse,
))


def _member_owned_content_scope(request: Request) -> tuple[str, str]:
    workspace_id = current_workspace_id(request)
    owner, _diagnostic = _content_identity_http_adapter().reader(request, workspace_id)
    if not owner:
        raise HTTPException(status_code=403, detail="请先确认当前成员身份，再操作私人素材。")
    return workspace_id, owner


app.include_router(build_voice_sampling_router(
    sampling_for=lambda: production_repo.voice_sampling,
    member_scope_for=lambda request: _member_owned_content_scope(request),
    companion_for=lambda request: companion_identity_for(request),
    provider_for=lambda: voice_clone_provider,
))


app.include_router(_profile_http_router(("list_media_voice_profiles",)))


app.include_router(_profile_http_router(("save_media_voice_profile",)))


app.include_router(_profile_http_router(("get_media_voice_profile",)))


app.include_router(_profile_http_router(("list_media_voice_profile_versions",)))


app.include_router(build_pronunciation_router(
    records_for=lambda: production_repo,
    workspace_for=lambda request: current_workspace_id(request),
    required_text_for=lambda value, field: _required_media_text(value, field),
    write_error_for=lambda exc: _media_write_error(exc),
))


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


app.include_router(build_media_capability_router(
    voice_for=lambda: voice_clone_provider,
    unavailable_for=lambda: _capability_unavailable,
    lipsync_for=lambda: _lipsync_provider,
    response_type_for=lambda: MediaCapabilityResponse,
))




def _media_preview_http_router():
    return build_preview_router(
        records_for=lambda: production_repo,
        workspace_for=lambda request: current_workspace_id(request),
        reader_for=lambda request, workspace_id: _content_identity_http_adapter().reader(request, workspace_id),
        actor_for=lambda request, workspace_id: _require_permit_actor(request, workspace_id),
        runtime_for=lambda: _creation_runtime(),
        voice_service_for=lambda: voice_preview_service,
        avatar_service_for=lambda: avatar_preview_service,
        output_root_for=lambda: settings.video_output_path,
    )


app.include_router(_media_preview_http_router())


def _material_catalog_http_adapter():
    return MaterialCatalogHTTPAdapter(
        records_for=lambda: production_repo,
        workspace_for=current_workspace_id,
    )


app.include_router(build_material_catalog_router(_material_catalog_http_adapter, ('list_material_collections',)))


app.include_router(build_material_catalog_router(_material_catalog_http_adapter, ('create_material_collection',)))


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
        path = production_repo.narration_artifacts.resolve_authorized_narration_audio_reference(
            workspace_id,
            narration_reference,
        )
    except MediaAuthorityError:
        return "unavailable"
    return "ready" if path.is_file() and path.stat().st_size > 0 else "unavailable"


def _legacy_montage_response_adapter():
    return LegacyMontageResponseAdapter(
        repository_for=lambda: production_repo,
        montage_repository_for=lambda: montage_repo,
        safe_montage_media_path=lambda plan, field: _safe_montage_media_path(plan, field),
        montage_narration_status=lambda workspace_id, reference: _montage_narration_status(workspace_id, reference),
    )








def _creation_plan_preflight_http_adapter():
    return CreationPlanPreflightHttpAdapter(
        case_for=lambda: CreationPlanPreflightUseCase(production_repo, source_repository, model_catalog,
            lipsync_provider_for=_lipsync_provider),
        read_template=lambda request, workspace_id, template_id, version: _video_template_http_adapter().read_template(request, workspace_id, template_id, version),
        content_reader=lambda request, workspace_id: _content_identity_http_adapter().reader(request, workspace_id),
    )



def _creation_task_response_adapter():
    return CreationTaskResponseAdapter(
        repository_for=lambda: production_repo,
        person_render_media_path=lambda render: _person_render_media_path(render),
    )




def _workspace_llm_route_snapshot():
    return WorkspaceLlmRouteSnapshot(
        settings_for=lambda workspace_id: _workspace_settings_for(workspace_id),
        clock=lambda: utc_now(),
    )




def _source_inputs_use_case() -> CreationSourceInputs:
    return CreationSourceInputs(source_repository)


def _creation_task_topic_generator_for(settings_for_workspace: Settings) -> OpenAICompatibleCreationTaskTopicGenerator:
    return OpenAICompatibleCreationTaskTopicGenerator(
        base_url=settings_for_workspace.effective_llm_base_url,
        model=settings_for_workspace.effective_llm_model,
        api_key=settings_for_workspace.effective_llm_api_key,
    )


def _creation_task_script_generator_for(settings_for_workspace: Settings) -> OpenAICompatibleCreationTaskScriptGenerator:
    if not settings_for_workspace.llm_configured:
        raise CreationTaskScriptGenerationError(
            code="route_configuration_invalid",
            reason="当前工作区尚未配置完整的文字模型连接。",
            impact="未调用文字模型，未生成或核查文案。",
            repair_action="请在当前工作区设置中连接文字模型，再继续核查文案。",
            retryable=False,
        )
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


def _creation_task_text_response_adapter():
    return CreationTaskTextResponseAdapter(
        repository_for=lambda: production_repo,
        topic_route_snapshot=lambda workspace_id: _workspace_llm_route_snapshot().snapshot(workspace_id),
    )








def _portrait_processor(workspace_id: str) -> LocalPortrait:
    return _picture_preview_renderer().portrait_processor(workspace_id)




def _exact_edit_access(task_id, request, *, write=False):
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    member_id = _content_identity_http_adapter().assistant_member(request, workspace_id)
    if not _content_identity_http_adapter().visible(request, workspace_id, task.visibility, task.owner_user_id):
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
    actor=(_request_permit_actor(request,task.workspace_id) if auth_context.enabled
        else CreationExecutionActor(task.workspace_id,member_id))
    try:
        return _exact_preview_use_case()._source(task,payload,
            {'member_id':member_id,'local_diagnostic':False},actor,None,request.headers.get('X-Assistant-Input-ID'))
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
        if not ids:
            return []
        reader,diagnostic=_content_identity_http_adapter().reader(request,task.workspace_id)
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


def _task_edit_receipt_http_adapter():
    return TaskEditReceiptHttpAdapter(
        repository_for=lambda: production_repo,
        exact_preview_for=lambda: _exact_preview_use_case(),
        montage_response=lambda plan: _creation_task_response_adapter().creation_task_montage_response(plan),
        wake=lambda: _wake_assistant_continuations(),
    )








def _task_edit_http_router(route_names):
    return build_task_edit_router(
        edit_controls_for=lambda: _edit_control_use_case,
        audio_command_preview_for=lambda: AudioCommandPreview,
        audio_edit_rejected_for=lambda: AudioEditRejected,
        cover_operation_for=lambda: COVER_OPERATION,
        clip_preview_command_for=lambda: ClipPreviewCommand,
        clip_preview_conflict_for=lambda: ClipPreviewConflict,
        clip_preview_unavailable_for=lambda: ClipPreviewUnavailable,
        command_parse_rejected_for=lambda: CommandParseRejected,
        creation_execution_actor_for=lambda: CreationExecutionActor,
        creation_task_edit_preview_request_for=lambda: CreationTaskEditPreviewRequest,
        exact_edit_source_for=lambda: EXACT_EDIT_SOURCE,
        edit_preview_rejected_for=lambda: EditPreviewRejected,
        goal_projection_rejected_for=lambda: GoalProjectionRejected,
        http_exception_for=lambda: HTTPException,
        host_budget_uncalibrated_for=lambda: HostBudgetUncalibrated,
        json_response_for=lambda: JSONResponse,
        linked_edit_operations_for=lambda: LINKED_EDIT_OPERATIONS,
        picture_preview_rejected_for=lambda: PicturePreviewRejected,
        task_media_required_for=lambda: TaskMediaRequired,
        timeline_version_conflict_for=lambda: TimelineVersionConflict,
        assistant_conversation_for_request_for=lambda: _assistant_conversation_for_request,
        assistant_member_id_for=lambda: _content_identity_http_adapter().assistant_member,
        bind_assistant_edit_instruction_for=lambda: _bind_assistant_edit_instruction,
        claim_edit_request_for=lambda: _task_edit_receipt_http_adapter().claim_edit_request,
        command_preview_use_case_for=lambda: _command_preview_use_case,
        compound_edit_use_case_for=lambda: _compound_edit_use_case,
        compound_proposal_command_for=lambda: _task_edit_command_http_adapter().compound_proposal_command,
        confirm_cover_response_for=lambda: _cover_confirmation_http_adapter().confirm_cover_response,
        confirm_creation_task_edit_for=lambda: _task_edit_confirmation_http_adapter().confirm,
        confirm_linked_timeline_response_for=lambda: _metadata_edit_confirmation_http_adapter().confirm_linked_timeline_response,
        content_reader_for=lambda: _content_identity_http_adapter().reader,
        content_visible_for=lambda: _content_identity_http_adapter().visible,
        creation_runtime_for=lambda: _creation_runtime,
        creation_task_or_404_for=lambda: _creation_task_or_404,
        current_narration_snapshot_for=lambda: _current_narration_snapshot,
        edit_preview_response_for=lambda: _edit_preview_response,
        ensure_montage_base_version_for=lambda: _ensure_montage_base_version,
        exact_edit_access_for=lambda: _exact_edit_access,
        exact_preview_use_case_for=lambda: _exact_preview_use_case,
        linked_timeline_apply_command_for=lambda: _task_edit_command_http_adapter().linked_timeline_apply_command,
        linked_timeline_use_case_for=lambda: _linked_timeline_use_case,
        logical_montage_output_refs_for=lambda: _logical_montage_output_refs,
        media_result_receipt_for=lambda: _media_result_receipt,
        metadata_edit_command_for=lambda: _task_edit_command_http_adapter().metadata_edit_command,
        metadata_edit_response_for=lambda: _metadata_edit_response,
        original_edit_result_for=lambda: _task_edit_receipt_http_adapter().original_edit_result,
        read_edit_request_result_for=lambda: _task_edit_receipt_http_adapter().read_edit_request_result,
        request_permit_actor_for=lambda: _request_permit_actor,
        require_assistant_input_origin_for=lambda: _require_assistant_input_origin,
        require_permit_actor_for=lambda: _require_permit_actor,
        save_edit_request_result_for=lambda: _task_edit_receipt_http_adapter().save_edit_request_result,
        subtitle_edit_use_case_for=lambda: _subtitle_edit_use_case,
        subtitle_proposal_command_for=lambda: _task_edit_command_http_adapter().subtitle_proposal_command,
        topic_failure_response_for=lambda: _topic_failure_response,
        wake_assistant_continuations_for=lambda: _wake_assistant_continuations,
        auth_context_for=lambda: auth_context,
        conversation_repo_for=lambda: conversation_repo,
        creation_task_montage_plan_from_payload_for=lambda: creation_task_montage_plan_from_payload,
        creation_task_montage_plan_to_payload_for=lambda: creation_task_montage_plan_to_payload,
        current_workspace_id_for=lambda: current_workspace_id,
        is_subtitle_edit_for=lambda: is_subtitle_edit,
        json_for=lambda: json,
        media_digest_for=lambda: media_digest,
        production_repo_for=lambda: production_repo,
        route_names=route_names,
    )


app.include_router(_task_edit_http_router(('get_exact_edit_request_result',)))




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
    metadata=MetadataEditUseCase(repo,artifact_store,output_root=Path(settings.video_output_path),audio_service=audio,
        task_authorizations=_task_authorizations_for_goal(repo))
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
        runtime=_creation_runtime()
        media=runtime.registrations['creation_job'].owner.task_media
        try:
            values=case.prepare_queued_preview(task,actor,values,
                facts=media.facts,verify_identity=media.verify_identity)
            job=media.submit(runtime,actor,task_id,'audio_edit_preview',values,'edit_preview')
            receipt=_media_result_receipt(media,job,actor,runtime)
            receipt['result']=None
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
    try:
        with _audio_ingress_use_case().prepare_submission(task, actor, operation,
                filename=audio.filename if audio is not None else None,
                stream=audio.file if audio is not None else None,
                draft=request.query_params.get('draft') == 'true' or request.url.path.endswith('/edits/exact'),
                arguments=arguments, temporary_root=settings.effective_temporary_root_path,
                cpu_budget=lambda: _device_operation_budget("cpu_audio")) as values:
            runtime = _creation_runtime()
            media = runtime.registrations['creation_job'].owner.task_media
            job = media.submit(runtime, actor, task_id, operation, values, result_kind)
            receipt = _media_result_receipt(media, job, actor, runtime)
            receipt['result'] = None
            return JSONResponse(status_code=202, content=receipt)
    except AudioIngressRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc







def _visual_preview_facts(workspace_id, task_id, arguments, actor):
    case = _visual_preview_use_case()
    try:
        prepared = case.prepare_source(workspace_id, task_id, arguments, actor)
    except PicturePreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    case.require_picture_plan(prepared)
    budget = case.require_render_budget(_device_operation_budget("cpu_render"))
    if case.uses_presenter(prepared):
        presenter_cpu = _device_operation_budget("presenter_cpu_render")
        budget = case.require_presenter_budget(presenter_cpu)
    return case.seal_resources(prepared, budget, _device_operation_budget("cpu_audio"), actor=actor)


def _compound_selection_facts(workspace_id, task_id, arguments, actor):
    case = _compound_edit_use_case()
    prepared = case.prepare_source(workspace_id, task_id, arguments, actor)
    if prepared[1] is None:
        raise ValueError('当前任务没有可预览的故事板')
    budget = _device_operation_budget("cpu_render")
    if budget is None:
        raise ValueError('CPU 画面预览预算尚未标定，未启动媒体')
    if any(clip.person_render_id for clip in prepared[1].clips):
        presenter_cpu = _device_operation_budget("presenter_cpu_render")
        if presenter_cpu is None:
            raise ValueError('人物组合 CPU 预览预算尚未标定，未启动媒体')
        budget = presenter_cpu
    return case.seal_resources(prepared, budget, _device_operation_budget("cpu_audio"), actor=actor)



def _queue_exact_preview(task_id,request,payload,*,source,media_kind):
    workspace_id=current_workspace_id(request)
    actor=_require_permit_actor(request,workspace_id)
    case=_exact_preview_use_case()
    try:
        task,values=case.submission_arguments(workspace_id,task_id,actor,payload,media_kind=media_kind,
            draft_only=request.query_params.get('draft')=='true' or request.url.path.endswith('/edits/exact'),source=source)
        runtime=_creation_runtime()
        media=runtime.registrations['creation_job'].owner.task_media
        job=case.submit(runtime,media,actor,task,values)
        return _media_result_receipt(media,job,actor,runtime)
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403,detail=str(exc)) from exc
    except (ValueError,KeyError) as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


def _exact_preview_facts(workspace_id,task_id,arguments,actor):
    case=_exact_preview_use_case()
    prepared=case.prepare_source(workspace_id,task_id,arguments,actor)
    if prepared[1] is None:
        raise ValueError('当前任务没有可预览的故事板')
    budget=_device_operation_budget("cpu_render")
    if budget is None:
        raise ValueError('CPU 画面预览预算尚未标定，未启动媒体')
    if any(clip.person_render_id for clip in prepared[1].clips):
        person_budget=_device_operation_budget("presenter_cpu_render")
        if person_budget is None:
            raise ValueError('人物组合 CPU 预览预算尚未标定，未启动媒体')
        budget=person_budget
    resources=case.picture_resources(prepared,budget)
    return case.seal_resources(prepared,resources,_device_operation_budget("cpu_audio"),actor=actor)


def _task_media_facts(workspace_id, task_id, operation, arguments, actor, *, task_authorizations=None):
    if operation == 'compound_selection_preview':
        return _compound_selection_facts(workspace_id, task_id, arguments, actor)
    if operation == 'visual_edit_preview' and '_clip_preview' not in arguments:
        return _visual_preview_facts(workspace_id, task_id, arguments, actor)
    if operation in MEDIA_SCRIPT_ACTIONS:
        owner = _media_script_use_case(task_id=task_id, task_authorizations=task_authorizations)
        prepared = owner.prepare_source(workspace_id, task_id, operation, arguments, actor)
        toolchain, budget = _script_configuration()
        return owner.seal(prepared, toolchain, budget, arguments.get('_script_toolchain'))
    if operation == 'output_observation':
        return _output_observation_facts(workspace_id, task_id, arguments, actor)
    if AudioEditUseCase.handles(operation, arguments):
        return _audio_edit_use_case().seal(workspace_id, task_id, operation, arguments, actor, _device_operation_budget("cpu_audio"))
    if operation in AUDIO_INGRESS_ACTIONS:
        return _audio_ingress_use_case().seal(workspace_id, task_id, operation, arguments, actor, _device_operation_budget("cpu_audio"))
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














def _media_results():
    return MediaResultUseCase(production_repo, _output_observation_use_case())


def _media_result_binding():
    return AssistantMediaResultBinding(production_repo, conversation_repo, _goal_projection(),
        artifact_store, _output_observation_use_case())


def _media_result_receipt(media, job, actor, runtime):
    try:
        return _media_results().receipt(media, job, actor, runtime.scheduler)
    except MediaResultRejected as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


def _media_result_read_components():
    from app.creation_execution import CreationJobOwnerVerifier
    from app.device_runtime import OwnerReadRouter
    from app.device_scheduler import DeviceRequestReadAccess
    from app.member_permits import MemberDeviceAccess

    with _runtime_reload_lock:
        runtime = device_runtime
        if runtime is not None:
            registration = runtime.registrations.get('creation_job')
            if runtime.last_error is not None or registration is None:
                raise HTTPException(status_code=503, detail="整机制作许可已停止，须核查许可后才能继续")
            return registration.owner.task_media, runtime.scheduler
        repository = production_repo
        authorizations = _task_authorizations_for_goal(repository)
        media = TaskMediaJobs(repository,
            known_failure_secrets_for=lambda workspace_id: (
                _workspace_settings_for(workspace_id).effective_llm_api_key,
                settings.tencentcloud_secret_id, settings.tencentcloud_secret_key),
            facts=lambda *args: _task_media_facts(*args, task_authorizations=authorizations),
            verify_identity=_verify_creation_identity,
            clip_preview=ClipPreviewExecutor(_clip_preview_use_case(repository),
                TaskAudio(repository, artifact_store, settings.effective_temporary_root_path)))
        media.task_authorizations = authorizations
        owner = CreationJobOwnerVerifier(repository, verify_identity=_verify_creation_identity,
            task_media=media, task_authorizations=authorizations)
        access = MemberDeviceAccess(local_members_service, identity_verifier=_verify_creation_identity)
        owners = OwnerReadRouter(lambda kind: owner if kind == 'creation_job' else None, access)
        return media, DeviceRequestReadAccess(repository.database_path, owners=owners)



def _read_media_result(task_id, job_id, request):
    actor = _require_permit_actor(request, current_workspace_id(request))
    media, requests = _media_result_read_components()
    try:
        return _media_results().read(media, task_id, job_id, actor, requests)
    except MediaResultRejected as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


def _related_media_result(task_id, request, turn_id, conversation_id, reported):
    # Schema validation precedes runtime acquisition, as in the original gate.
    related = TaskMediaJobResponse.model_validate(reported)
    workspace_id = current_workspace_id(request)
    media, _requests = _media_result_read_components()
    try:
        job_id = _media_result_binding().related_job(media, workspace_id, task_id, turn_id, conversation_id, related)
    except MediaResultRejected as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return TaskMediaJobResponse.model_validate(_read_media_result(task_id, job_id, request))


def _completed_media_result(task, request, turn_id, slot, *, contract, input_refs):
    actor = _require_permit_actor(request, task.workspace_id)
    try:
        return _media_result_binding().completed_receipt(task, actor, turn_id, slot, contract=contract, input_refs=input_refs)
    except MediaResultRejected as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


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
        from app.assistant.goal_versions import observation_turn_origin
        try:
            origin = observation_turn_origin(production_repo, conversation_repo, task, member_id,
                str(payload.assistant_turn_id), version)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    budget = _device_operation_budget("cpu_render")
    case.require_render_budget(budget)
    observation_budget = _device_operation_budget("observation")
    return case.seal_resources(source, budget, observation_budget)




def _task_observation_response_adapter():
    return TaskObservationResponseAdapter(
        repo_for=lambda: production_repo,
        quality_response=lambda workspace_id, task_id: _creation_task_quality_check_response(workspace_id, task_id),
        registered_output=lambda workspace_id, task_id, target, actor: _registered_observation_output(workspace_id, task_id, target, actor),
        quality_history=lambda workspace_id, task_id: quality_check_repo.history(workspace_id, task_id),
    )




def _task_observation_http_router(route_names):
    return build_task_observation_router(
        access_for=lambda: _exact_edit_access,
        metadata_for=lambda: _task_observation_response_adapter().metadata,
        actor_for=lambda: _require_permit_actor,
        case_for=lambda: _output_observation_use_case,
        json_response_for=lambda: JSONResponse,
        prepare_for=lambda: _prepare_observation_request,
        runtime_for=lambda: _creation_runtime,
        receipt_for=lambda: _media_result_receipt,
        output_error_for=lambda: OutputObservationRejected,
        http_error_for=lambda: HTTPException,
        route_names=route_names,
    )


app.include_router(_task_observation_http_router(("get_creation_task_observations",)))


app.include_router(_task_observation_http_router(("observe_creation_task_output",)))


def _task_media_http_router(route_names):
    return build_task_media_router(
        workspace_for=lambda: current_workspace_id,
        actor_for=lambda: _require_permit_actor,
        task_for=lambda: _creation_task_or_404,
        list_for=lambda: _list_goal_media_jobs,
        read_for=lambda: _read_media_result,
        read_components_for=lambda: _media_result_read_components,
        runtime_for=lambda: _creation_runtime,
        repository_for=lambda: production_repo,
        receipt_for=lambda: _media_result_receipt,
        observations_for=lambda: _output_observation_use_case,
        route_names=route_names,
    )


app.include_router(_task_media_http_router(("list_task_media_jobs",)))


def _list_goal_media_jobs(task, actor, *, cursor=None, limit=20):
    media, requests = _media_result_read_components()
    try:
        return _media_results().list(media, task, actor, requests, cursor=cursor, limit=limit)
    except MediaResultRejected as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


app.include_router(_task_media_http_router(("get_task_media_job",)))


app.include_router(_task_media_http_router(("cancel_task_media_job",)))


app.include_router(_task_media_http_router(("retry_task_media_job",)))


def _picture_preview_components(repository=None, *, write_guard=None):
    repo = repository if repository is not None else production_repo
    audio = TaskAudio(repo, artifact_store, settings.effective_temporary_root_path)
    metadata = MetadataEditUseCase(repo, artifact_store, output_root=Path(settings.video_output_path), audio_service=audio,
        task_authorizations=_task_authorizations_for_goal(repo), write_guard=write_guard,
        validate_origin=lambda source,actor,connection=None:validate_edit_origin(conversation_repo,source,actor,connection=connection))
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
        NarrationPreviewUseCase(repo,metadata),PersonPreviewUseCase(repo,metadata,latentsync_provider,avatar_lipsync_provider), budgets_for=_device_host_budget)


def _person_preview_use_case():
    return PersonPreviewUseCase(production_repo,_metadata_edit_use_case(),latentsync_provider,avatar_lipsync_provider)


def _exact_preview_use_case(repository=None, *, write_guard=None):
    repo = production_repo if repository is None else repository
    audio, metadata, renderer = _picture_preview_components(repo, write_guard=write_guard)
    person=PersonPreviewUseCase(repo,metadata,latentsync_provider,avatar_lipsync_provider)
    return ExactPreviewUseCase(repo, metadata, SubtitleEditUseCase(repo,metadata),
        LinkedTimelineEditUseCase(repo,metadata), AudioEditUseCase(repo,audio,metadata,creation_execution_mode),
        VisualPreviewUseCase(repo,artifact_store,audio,metadata,renderer), _clip_preview_use_case(repo), conversation_repo, settings, edit_result=_metadata_edit_projection,
        preview_result=lambda intent, proposal: _edit_preview_response(intent, proposal).model_dump(mode="json"),
        person_preview=person,narration_preview=NarrationPreviewUseCase(repo,metadata),
        person_stage=PersonStageUseCase(repo,metadata,person,video_job_runner,settings),
        execution_control=_creation_execution_control,runtime_for=_creation_runtime)


def _visual_preview_use_case(repository=None):
    repo = repository if repository is not None else production_repo
    audio, metadata, renderer = _picture_preview_components(repo)
    return VisualPreviewUseCase(repo, artifact_store, audio, metadata, renderer)


def _picture_preview_renderer():
    return _picture_preview_components()[2]


def _task_audio(repository=None) -> TaskAudio:
    return _picture_preview_components(repository)[0]


def _clip_preview_use_case(repository=None):
    repo = production_repo if repository is None else repository
    def profile():
        try:
            return _device_operation_budget("clip_preview")
        except HostBudgetUncalibrated as exc:
            raise ClipPreviewUnavailable(str(exc)) from exc
    def presenter_source(task, clip):
        from app.creation.person_stage_use_case import adopted_person_render_source
        plan = creation_task_montage_plan_from_payload(repo.get_creation_task_montage_plan(task.workspace_id, task.id))
        cue = next((item for item in plan.cues if item.id == clip.cue_id), None)
        return adopted_person_render_source(repo, task, clip, cue, _person_render_media_path)
    return ClipPreviewUseCase(repo, resolve_presenter_source=presenter_source, budget=profile)


def _clip_preview_submission_http_adapter():
    return ClipPreviewSubmissionHttpAdapter(
        current_workspace=lambda request: current_workspace_id(request),
        task404=lambda workspace_id, task_id: _creation_task_or_404(workspace_id, task_id),
        draft_metadata=lambda task, request, operation_type, command_values, base_version_id: _preview_metadata_settings(task, request, operation_type, command_values, base_version_id),
        permit_actor=lambda request, workspace_id: _require_permit_actor(request, workspace_id),
        exact_source_getter=lambda: EXACT_EDIT_SOURCE.get(),
        repository_for=lambda: production_repo,
        source_facts=lambda task, action, request: _exact_edit_source_facts(task, action, request),
        task_turn_source=lambda turn_id, workspace_id, member_id, task_id: conversation_repo.task_turn_source(turn_id, workspace_id, member_id, task_id),
        require_input_origin=lambda request, source: _require_assistant_input_origin(request, source),
        clip_case_for=lambda: _clip_preview_use_case(),
        runtime_for=lambda: _creation_runtime(),
        task_audio_for=lambda: _task_audio(),
        media_receipt=lambda media, job, actor, runtime: _media_result_receipt(media, job, actor, runtime),
    )




def _task_audio_http_router(route_names):
    return build_task_audio_router(
        submit_for=lambda: _submit_audio_ingress,
        workspace_for=lambda: current_workspace_id,
        task_for=lambda: _creation_task_or_404,
        ingress_for=lambda: _audio_ingress_use_case,
        outputs_for=lambda: _output_observation_use_case,
        ingress_error_type_for=lambda: AudioIngressRejected,
        http_error_type_for=lambda: HTTPException,
        narration_for=lambda: _creation_task_response_adapter().creation_task_narration_response,
        records_for=lambda: production_repo,
        media_kind_for=lambda: TASK_MEDIA_KIND,
        actor_for=lambda: _require_permit_actor,
        runtime_for=lambda: _creation_runtime,
        actor_type_for=lambda: CreationExecutionActor,
        audio_for=lambda: _task_audio,
        file_response_for=lambda: FileResponse,
        preview_for=lambda: _preview_audio_edit_request,
        route_names=route_names,
    )


app.include_router(_task_audio_http_router(("import_creation_task_audio",)))


app.include_router(_task_audio_http_router(("list_creation_task_recordings",)))


app.include_router(_task_audio_http_router(("save_creation_task_recording",)))


app.include_router(_task_audio_http_router(("adopt_creation_task_recording",)))




app.include_router(_task_audio_http_router(("get_creation_task_audio_artifact",)))


app.include_router(_task_audio_http_router(("get_creation_task_audio_waveform",)))


app.include_router(_task_audio_http_router(("listen_creation_task_audio",)))


def _preview_clip_original_audio(task_id: str, request: Request, payload: CreationTaskEditPreviewRequest):
    return _preview_audio_edit_request(task_id, request, payload, original_clip=True, queue_on_required=False)


app.include_router(_task_audio_http_router(("preview_creation_task_audio",)))


def _task_visual_http_router(route_names):
    return build_task_visual_router(
        workspace_for=lambda: current_workspace_id,
        task_for=lambda: _creation_task_or_404,
        import_for=lambda: import_graphic_source,
        records_for=lambda: production_repo,
        store_for=lambda: artifact_store,
        settings_for=lambda: settings,
        http_error_type_for=lambda: HTTPException,
        access_for=lambda: _exact_edit_access,
        sources_for=lambda: graphic_sources,
        resolve_source_for=lambda: resolve_graphic_source,
        file_response_for=lambda: FileResponse,
        media_required_type_for=lambda: TaskMediaRequired,
        actor_for=lambda: _require_permit_actor,
        runtime_for=lambda: _creation_runtime,
        plan_from_for=lambda: creation_task_montage_plan_from_payload,
        base_for=lambda: _ensure_montage_base_version,
        receipt_for=lambda: _media_result_receipt,
        json_response_for=lambda: JSONResponse,
        preview_case_for=lambda: _visual_preview_use_case,
        needs_full_for=lambda: needs_full_visual_preview,
        clip_submit_for=lambda: _clip_preview_submission_http_adapter().submit,
        edit_response_for=lambda: _edit_preview_response,
        picture_error_type_for=lambda: PicturePreviewRejected,
        route_names=route_names,
    )


app.include_router(_task_visual_http_router(("import_creation_task_graphic",)))


def _script_configuration():
    toolchain = read_script_toolchain(Path(settings.model_root_path).expanduser())
    try:
        budget = _device_operation_budget("media_script")
    except (ValueError, HostBudgetUncalibrated) as exc:
        raise MediaScriptError("媒体脚本专用预算不完整或不可核验") from exc
    if budget is None:
        raise MediaScriptError("媒体脚本专用预算尚未标定")
    return toolchain, budget







def _task_media_script_http_router(route_names):
    return build_task_media_script_router(
        access_for=lambda: _exact_edit_access,
        facts_for=lambda: _task_script_capability_facts,
        command_for=lambda: media_script_submission_command,
        runner_for=lambda: _media_script_submission_http_adapter().run,
        submission_for=lambda: _media_script_submission_use_case,
        json_response_for=lambda: JSONResponse,
        route_names=route_names,
    )


app.include_router(_task_media_script_http_router(('get_media_script_capabilities',)))


def _task_script_capability_facts(task, member_id):
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


def _media_script_submission_http_adapter():
    def bind_recipe_selection(runtime, actor, task_id, arguments):
        task = production_repo.task_records.get_creation_task(actor.workspace_id, task_id)
        authorizations = runtime.task_authorizations
        authorizations.ensure(task, actor)
        definition_ref, recipe_current = _media_script_tools().prepare_task_selection(actor,
            arguments['tool_id'], version=arguments['payload']['tool_version'],
            expected_reference=arguments['_recipe_ref'])
        commit = authorizations.prepare_recipe_selection(task, actor, definition_ref,
            source=arguments['_typed_source'],
            request_inputs={**arguments['payload'], 'tool_id': arguments['tool_id']}, recipe_current=recipe_current)
        with production_repo._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            commit(connection)

    return MediaScriptSubmissionHttpAdapter(
        exact_edit_access=lambda task_id, request, **values: _exact_edit_access(task_id, request, **values),
        current_workspace_id=lambda request: current_workspace_id(request),
        require_permit_actor=lambda request, workspace_id: _require_permit_actor(request, workspace_id),
        request_permit_actor=lambda request, workspace_id: _request_permit_actor(request, workspace_id),
        auth_enabled=lambda: auth_context.enabled,
        require_assistant_input_origin=lambda request, source: _require_assistant_input_origin(request, source),
        creation_task_or_404=lambda workspace_id, task_id: _creation_task_or_404(workspace_id, task_id),
        script_configuration=lambda: _script_configuration(),
        creation_runtime=lambda: _creation_runtime(),
        media_result_receipt=lambda media, job, actor, runtime: _media_result_receipt(media, job, actor, runtime),
        bind_recipe_selection=bind_recipe_selection,
        wake=lambda: _wake_assistant_continuations(),
    )




def _expand_media_script_submission_recipe(actor, tool_id, values):
    expanded, reference = _media_script_use_case().expand_recipe(actor, tool_id,
        MediaScriptToolPreviewRequest.model_validate(values))
    return script_submission.RecipeExpansion(media_script_submission_source(expanded), reference)


def _completed_script_submission_receipt(task, actor, turn_id, slot, *, contract, input_refs):
    try:
        return _media_result_binding().completed_receipt(task, actor, turn_id, slot, contract=contract, input_refs=input_refs)
    except MediaResultRejected as exc:
        raise script_submission.ScriptSubmissionRejected(exc.status_code, exc.detail) from exc


def _media_script_submission_use_case():
    return script_submission.MediaScriptSubmissionUseCase(request_receipts=_exact_preview_use_case,
        read_version=production_repo.timeline_records.get_timeline_version, conversations=conversation_repo,
        expand_recipe=_expand_media_script_submission_recipe,
        completed_receipt=_completed_script_submission_receipt)




app.include_router(_task_media_script_http_router(('preview_media_script',)))


def _media_script_use_case(repository=None, *, task_id=None, task_authorizations=None):
    repo = repository if repository is not None else production_repo
    return MediaScriptPreviewUseCase(repo, artifact_store, settings.effective_temporary_root_path, _media_script_tools(repo, task_id=task_id, task_authorizations=task_authorizations))



def _media_script_tools(repository=None, *, task_id=None, task_authorizations=None):
    repo = repository if repository is not None else production_repo
    def authorize(actor, task):
        user_id = actor.user_id or actor.member_id
        return (actor.workspace_id == task.workspace_id and (task.owner_user_id is None or task.owner_user_id == user_id)
            and content_readable(user_id=user_id, local_diagnostic=False, visibility=task.visibility, owner_user_id=task.owner_user_id))
    return MediaScriptTools(repo, artifact_store, verify_identity=_verify_creation_identity,
        authorize_task_write=authorize, task_id=task_id,
        task_authorizations=(task_authorizations if task_authorizations is not None else
            device_runtime.task_authorizations if task_id is not None and device_runtime is not None else None))


def _script_tool_error(exc):
    status = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, KeyError) else 409 if isinstance(exc, (ScriptToolConflict, ScriptToolUnknown)) else 422
    raise HTTPException(status_code=status, detail="配方不存在或不可访问" if isinstance(exc, KeyError) else str(exc)) from exc



def _media_script_tool_http_router(route_names):
    return build_media_script_tool_router(
        access_for=lambda: _exact_edit_access,
        actor_for=lambda: _require_permit_actor,
        conversations_for=lambda: conversation_repo,
        origin_for=lambda: _require_assistant_input_origin,
        repository_for=lambda: production_repo,
        tools_for=lambda: _media_script_tools,
        script_error_type_for=lambda: ScriptToolError,
        error_for=lambda: _script_tool_error,
        workspace_for=lambda: current_workspace_id,
        capabilities_for=lambda: _script_tool_capability_facts,
        command_for=lambda: media_script_submission_command,
        runner_for=lambda: _media_script_submission_http_adapter().run,
        submission_for=lambda: _media_script_submission_use_case,
        json_response_for=lambda: JSONResponse,
        http_error_type_for=lambda: HTTPException,
        route_names=route_names,
    )


app.include_router(_media_script_tool_http_router(("save_media_script_tool",)))


app.include_router(_media_script_tool_http_router(("get_media_script_tool_creation",)))


app.include_router(_media_script_tool_http_router(("media_script_tool_capabilities",)))


def _script_tool_capability_facts():
    return {"schemas": {"save": MediaScriptToolSaveRequest.model_json_schema(),
        "update": MediaScriptToolUpdateRequest.model_json_schema(), "revoke": MediaScriptToolRevokeRequest.model_json_schema(),
        "invoke": MediaScriptToolPreviewRequest.model_json_schema()},
        "parameter_schema_subset": "有界object与scalar子集；实际参数由共同Leaf validator校验，不加载工具代码到host。",
        "invocation_phase": "script", "result_kind": "graphic_source"}


app.include_router(_media_script_tool_http_router(("list_media_script_tools",)))


app.include_router(_media_script_tool_http_router(("get_media_script_tool",)))


app.include_router(_media_script_tool_http_router(("update_media_script_tool",)))


app.include_router(_media_script_tool_http_router(("revoke_media_script_tool",)))


app.include_router(_media_script_tool_http_router(("preview_media_script_tool",)))


app.include_router(_task_visual_http_router(("get_creation_task_graphic_sources",)))


app.include_router(_task_visual_http_router(("get_creation_task_graphic_source",)))


app.include_router(_task_visual_http_router(("get_creation_task_visual_preview",)))


def _visual_preview_projection(plan, clip_ids, patch, tracks=()):
    return _visual_preview_use_case().project(plan, clip_ids, patch, tracks)


app.include_router(_task_visual_http_router(("preview_creation_task_visual",)))






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


def _task_workspace_read_use_case() -> TaskWorkspaceReadUseCase:
    repo = production_repo
    return TaskWorkspaceReadUseCase(
        read_plan=production_repo.plan_records.get_creation_plan,
        read_topics=production_repo.topic_records.list_topic_candidates,
        read_topic_evidence=production_repo.topic_records.list_topic_evidence_references,
        read_narration=production_repo.narration_artifacts.get_narration_artifact,
        read_montage=production_repo.get_creation_task_montage_plan,
        read_summary=lambda task: project_task_summary(production_repo, task),
        person_provider=_lipsync_provider,
        read_subtitles=lambda ws, task, **values: _metadata_edit_use_case().current_subtitle_tracks(ws, task, **values),
        read_covers=production_repo.cover_records.list_cover_assets,
        translation_provider=lambda: subtitle_translation_provider_from_settings(settings) if isinstance(settings, Settings) else None,
        read_events=production_repo.execution_records.list_task_events,
        find_tool=shutil.which,
        image_capability=unconfigured_image_generation_capability,
        read_script_headers=production_repo.script_records.active_creation_task_script_headers,
        read_voices=lambda workspace_id: repo.profile_records.list_versioned_voice_profiles(workspace_id),
        profile_readable=production_repo.profile_readable,
        read_person=lambda workspace_id, person_id, version=None: repo.profile_records.get_person_profile(workspace_id, person_id, version),
        read_voice_reference=production_repo.creation_plan_voice_reference,
        read_pronunciations=lambda *, workspace_id, person_profile_id, episode_id: repo.profile_records.list_applicable_pronunciation_entries(
            workspace_id=workspace_id, person_profile_id=person_profile_id, episode_id=episode_id),
        portrait_available=lambda ws: _portrait_processor(ws).available,
        read_person_renders=lambda workspace_id, task_id: repo.person_renders.list_person_renders(workspace_id, task_id),
    )


def _task_workspace_response_adapter():
    return TaskWorkspaceResponseAdapter(
        reader_for=lambda: _task_workspace_read_use_case(),
        encode_plan=lambda plan: _creation_task_response_adapter().creation_plan_preview_response(plan),
        encode_narration=lambda task, artifact: _creation_task_response_adapter().creation_task_narration_response(task, artifact),
        encode_person_render=lambda render: _creation_task_response_adapter().person_render_response(render),
        encode_montage=lambda montage: _creation_task_response_adapter().creation_task_montage_response(montage),
    )
def _assistant_profile_snapshot(request: Request, kind: str, profile_id: str, version: str) -> dict:
    workspace_id = current_workspace_id(request)
    user_id, diagnostic = _content_identity_http_adapter().reader(request, workspace_id)
    try:
        profile = (production_repo.profile_records.get_person_profile(workspace_id, profile_id, version) if kind == "person"
                   else production_repo.profile_records.get_versioned_voice_profile(workspace_id, profile_id, version))
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




def _assistant_reference_http_router(route_names):
    return build_assistant_reference_router(
        member_for=lambda: _content_identity_http_adapter().assistant_member,
        workspace_for=lambda: current_workspace_id,
        profile_snapshot_for=lambda: _assistant_profile_snapshot,
        knowledge_snapshot_for=lambda: _assistant_knowledge_snapshot,
        route_names=route_names,
    )


app.include_router(_assistant_reference_http_router(("assistant_profile_reference",)))


def _assistant_knowledge_snapshot(request: Request, knowledge_item_id: str, version: str, locator: str | None = None, *, include_content: bool = False) -> dict:
    workspace_id = current_workspace_id(request)
    user_id, diagnostic = _content_identity_http_adapter().reader(request, workspace_id)
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


app.include_router(_assistant_reference_http_router(("assistant_knowledge_reference",)))


def _home_plan_turn_use_case():
    return home_plan.HomePlanTurnUseCase(
        get_turn=conversation_repo.get_turn,
        finish_turn=conversation_repo.finish_turn,
        find_request_by_key=production_repo.creation_requests.find_creation_request_by_key)


def _assistant_creation_request_for_turn(request: Request, turn: dict) -> dict | None:
    steps = _home_plan_turn_use_case().recover_request(turn)
    stage = next(steps)
    while True:
        if isinstance(stage, home_plan.ReadConversationOwner):
            answer = _assistant_conversation_for_request(request, stage.conversation_id)[0]
        elif isinstance(stage, home_plan.ReadCurrentReader):
            answer = _content_identity_http_adapter().reader(request, stage.workspace_id)[0]
        else:
            raise TypeError(f"Unsupported home request recovery stage: {type(stage).__name__}")
        try:
            stage = steps.send(answer)
        except StopIteration as completed:
            return completed.value




def _assistant_conversation_for_request(request: Request, conversation_id: str) -> tuple[str, str, dict]:
    workspace_id = current_workspace_id(request)
    member_id = _content_identity_http_adapter().assistant_member(request, workspace_id)
    row = conversation_repo.get(conversation_id, workspace_id, member_id)
    if row is None:
        raise HTTPException(status_code=404, detail="对话不存在或当前成员无权访问。")
    return workspace_id, member_id, row
















def _conversation_http_adapter():
    return ConversationHTTPAdapter(
        conversations_for=lambda: conversation_repo,
        workspace_for=current_workspace_id, member_for=_content_identity_http_adapter().assistant_member,
        validate_scope=_validate_assistant_scope,
        owned_conversation=_assistant_conversation_for_request,
        clean_text=_assistant_text_without_credentials,
        validate_reference=_validate_saved_assistant_reference,
        recover_request=_assistant_creation_request_for_turn,
        require_actor=_require_permit_actor,
        segment_for=_segment_production_use_case, restore_for=_timeline_restore_use_case,
        input_context=lambda request, context, reference: _task_turn_http_adapter().input_context(request, context, reference),
        preflight=_creation_plan_preflight_http_adapter().preflight, render_plan=_creation_task_response_adapter().creation_plan_preview_response,
    )


app.include_router(build_conversation_router(_conversation_http_adapter))


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
                subtitle_tracks=production_repo.subtitle_records.list_subtitle_tracks(workspace_id, task.id, timeline_version_id=version.id))
        except AssistantReferenceConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        _assistant_profile_snapshot(request, reference["kind"], reference["profile_id"], reference["version"])
    return reference





















app.include_router(build_assistant_capability_router(
    workspace_for=lambda: current_workspace_id,
    member_for=lambda: _content_identity_http_adapter().assistant_member,
    checks_for=lambda: pi_runtime_checks,
    tools_for=lambda: ASSISTANT_BUSINESS_TOOLS,
))














def _workspace_assistant_turn_http_router(route_names):
    return build_workspace_assistant_turn_router(
        workspace_for=lambda: current_workspace_id,
        stream_for=lambda: _assistant_stream_transport,
        plan_for=lambda: _home_plan_http_adapter().execute,
        member_for=lambda: _content_identity_http_adapter().assistant_member,
        conversations_for=lambda: conversation_repo,
        selection_for=lambda: _assistant_material_selection_snapshot,
        plan_request_for=lambda: WorkspaceAssistantPlanTurnRequest,
        material_response_for=lambda: MaterialAssistantTurnResponse,
        route_names=route_names,
    )


app.include_router(_workspace_assistant_turn_http_router(('workspace_assistant_plan_turn',)))




def _home_plan_http_adapter():
    return HomePlanTurnHTTPAdapter(
        creation_plan_preview_response_type_for=lambda: CreationPlanPreviewResponse,
        creation_request_response_type_for=lambda: CreationRequestResponse,
        http_exception_type_for=lambda: HTTPException,
        material_assistant_result_response_type_for=lambda: MaterialAssistantResultResponse,
        workspace_assistant_plan_turn_response_type_for=lambda: WorkspaceAssistantPlanTurnResponse,
        assistant_creation_request_for_turn_for=lambda: _assistant_creation_request_for_turn,
        assistant_knowledge_snapshot_for=lambda: _assistant_knowledge_snapshot,
        assistant_profile_snapshot_for=lambda: _assistant_profile_snapshot,
        assistant_stream_safe_text_for=lambda: _assistant_stream_safe_text,
        assistant_text_without_credentials_for=lambda: _assistant_text_without_credentials,
        content_reader_for=lambda: _content_identity_http_adapter().reader,
        creation_request_or_404_for=lambda: _creation_request_or_404,
        creation_request_response_for=lambda: _creation_request_response,
        home_plan_turn_use_case_for=lambda: _home_plan_turn_use_case,
        local_member_token_for=lambda: _local_member_token,
        register_adopted_creation_goals_for=lambda: _register_adopted_creation_goals,
        task_turn_http_adapter_for=lambda: _task_turn_http_adapter,
        workspace_settings_for_for=lambda: _workspace_settings_for,
        current_workspace_id_for=lambda: current_workspace_id,
        forget_turn_control_for=lambda: forget_turn_control,
        home_plan_for=lambda: home_plan,
        run_plan_turn_for=lambda: run_plan_turn,
    )


app.include_router(_workspace_assistant_turn_http_router(('material_assistant_turn',)))


def _assistant_material_selection_snapshot(request: Request, selection: dict, *, replay: bool = False) -> tuple[dict, object]:
    selection = dict(selection)
    asset = _material_library_http_adapter().get_production_material(selection["asset_id"], request)
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
        asset = _material_library_http_adapter().get_production_material(clip.asset_id, request)
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
    task = creation_task_from_payload(task_payload) if task_payload else None
    return {**item, "plan": _creation_task_response_adapter().creation_plan_preview_response(
        production_repo.plan_records.creation_plan_from_payload(plan)
    ).model_dump(mode="json") if plan else None,
        "task": project_task_summary(production_repo, task).summary if task else None}


def _creation_request_or_404(request: Request, request_id: str) -> dict:
    workspace_id = current_workspace_id(request)
    owner, diagnostic = _content_identity_http_adapter().reader(request, workspace_id)
    if not owner:
        raise HTTPException(status_code=403, detail="请先确认当前成员，再读取制作目标。")
    try:
        return production_repo.creation_requests.get_creation_request(
            workspace_id, request_id, viewer_user_id=owner, local_diagnostic=diagnostic,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="没有找到当前成员的制作目标。") from exc






















def _creation_request_http_router(route_names):
    return build_creation_request_router(
        workflow_module_for=lambda: request_workflow,
        production_for=lambda: production_repo,
        preflight_type_for=lambda: CreationPlanPreflightUseCase,
        sources_for=lambda: source_repository,
        catalog_for=lambda: model_catalog,
        lipsync_for=lambda: _lipsync_provider,
        plan_request_type_for=lambda: CreationPlanPreviewRequest,
        preflight_input_type_for=lambda: CreationPlanPreflightInput,
        workspace_for=lambda: current_workspace_id,
        reader_for=lambda: _content_identity_http_adapter().reader,
        owned_request_for=lambda: _creation_request_or_404,
        reply_for=lambda: _verified_assistant_reply,
        template_for=lambda: _video_template_http_adapter,
        window_request_type_for=lambda: CreationRequestWindowPreviewRequest,
        replace_for=lambda: replace,
        http_error_for=lambda: HTTPException,
        conversations_for=lambda: conversation_repo,
        conversation_for=lambda: _assistant_conversation_for_request,
        goal_for=lambda: _goal_service,
        authorizations_for=lambda: _task_authorizations_for_goal,
        safe_text_for=lambda: _assistant_stream_safe_text,
        wake_for=lambda: _wake_assistant_continuations,
        response_for=lambda: _creation_request_response,
        items_type_for=lambda: CreationRequestItemsResponse,
        item_for=lambda: _creation_request_item_response,
        route_names=route_names,
    )


app.include_router(_creation_request_http_router(("resume_assistant_creation_request",)))


app.include_router(_creation_request_http_router(("create_creation_request",)))


app.include_router(_creation_request_http_router(("read_current_input_creation_request",)))


app.include_router(_creation_request_http_router(("get_creation_request",)))


app.include_router(_creation_request_http_router(("revise_creation_request",)))


app.include_router(_creation_request_http_router(("preview_creation_request_window",)))



app.include_router(_creation_request_http_router(("replan_creation_request_window",)))


app.include_router(_creation_request_http_router(("confirm_creation_request_window",)))


app.include_router(_creation_request_http_router(("list_creation_request_items",)))




app.include_router(build_creation_plan_router(
    preview_response_for=lambda: _creation_task_response_adapter().creation_plan_preview_response,
    preflight_for=lambda: _creation_plan_preflight_http_adapter().preflight,
    workspace_for=lambda: current_workspace_id,
    http_error_for=lambda: HTTPException,
    reply_for=lambda: _verified_assistant_reply,
    reader_for=lambda: _content_identity_http_adapter().reader,
    now_for=lambda: utc_now,
    uuid_for=lambda: uuid4,
    task_type_for=lambda: CreationTask,
    production_for=lambda: production_repo,
    plan_response_for=lambda: _creation_task_response_adapter().creation_plan_response,
    write_guard_for=lambda: _local_member_write_guard,
))


def _source_task_origin(request, turn_id, actor, request_id, payload):
    if turn_id is None:
        if request.headers.get("x-assistant-input-id"):
            raise HTTPException(409, "助手原片创作缺少原回合身份")
        return None, None
    from app.assistant.source_task_binding import SourceTaskBinding
    return SourceTaskBinding(conversation_repo).prepare(
        str(turn_id), request.headers.get("x-assistant-input-id") or None, actor, request_id, payload)


app.include_router(build_source_task_router(
    actor_for=lambda request: _require_permit_actor(request, current_workspace_id(request)),
    case_for=lambda: SourceTaskUseCase(production_repo, _verify_creation_identity),
    origin_for=_source_task_origin,
    input_for=lambda actor, request_id: conversation_repo.read_source_task_input(
        actor.workspace_id, actor.user_id or actor.member_id, request_id),
))


def _bind_script_library_use_case(draft_use_case, reader):
    case = ScriptLibraryUseCase(production_repo, source_repository,
        reader=reader, draft_use_case=draft_use_case)
    draft_use_case.read_script_header = case.read_script_header
    draft_use_case.read_library_header = case.read_header
    return case


def _script_library_use_case(request: Request):
    workspace_id = current_workspace_id(request)
    def reader(ws):
        return _content_identity_http_adapter().reader(request, ws)
    draft_use_case = _script_draft_use_case(workspace_id,
        validate_inputs=lambda ws, plan, refs: _validate_script_input_sources(ws, plan, refs, *reader(ws)))
    return _bind_script_library_use_case(draft_use_case, reader)


app.include_router(build_script_library_router(
    use_case=_script_library_use_case, workspace_id=lambda request: current_workspace_id(request)))




def _task_workspace_http_router(route_names):
    return build_task_workspace_router(
        workspace_for=lambda: current_workspace_id,
        reader_for=lambda: _content_identity_http_adapter().reader,
        production_for=lambda: production_repo,
        private_for=lambda: _content_identity_http_adapter().include_private,
        list_response_for=lambda: CreationTasksResponse,
        summary_for=lambda: project_task_summary,
        write_guard_for=lambda: _local_member_write_guard,
        http_error_for=lambda: HTTPException,
        workspace_response_for=lambda: _task_workspace_response_adapter().response,
        task_for=lambda: _creation_task_or_404,
        readable_for=lambda: _readable_creation_task,
        route_names=route_names,
    )


app.include_router(_task_workspace_http_router(('list_creation_tasks',)))


def _creation_task_or_404(workspace_id: str, task_id: str) -> CreationTask:
    try:
        return production_repo.task_records.get_creation_task(workspace_id, task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="creation task not found") from exc


def _readable_creation_task(request: Request, task_id: str) -> CreationTask:
    workspace_id = current_workspace_id(request)
    task = _creation_task_or_404(workspace_id, task_id)
    if not _content_identity_http_adapter().visible(request, workspace_id, task.visibility, task.owner_user_id):
        raise HTTPException(status_code=404, detail="creation task not found")
    return task


def _creation_plan_for_task_or_404(
    workspace_id: str,
    task: CreationTask,
) -> CreationPlan:
    try:
        plan = production_repo.plan_records.get_creation_plan(workspace_id, task.plan_id)
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
    candidate_section_index: int | None = None,
    candidate_field: str | None = None,
    candidate_fragment_index: int | None = None,
    candidate_field_digest: str | None = None,
    review_issues: list[dict[str, object]] | None = None,
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
            **({"candidate_section_index": candidate_section_index} if candidate_section_index is not None else {}),
            **({"candidate_field": candidate_field} if candidate_field is not None else {}),
            **({"candidate_fragment_index": candidate_fragment_index} if candidate_fragment_index is not None else {}),
            **({"candidate_field_digest": candidate_field_digest} if candidate_field_digest is not None else {}),
            **({"review_issues": review_issues} if review_issues is not None else {}),
        },
    )




app.include_router(build_task_topic_router(
    workspace_for=lambda: current_workspace_id,
    task_for=lambda: _creation_task_or_404,
    plan_for=lambda: _creation_plan_for_task_or_404,
    route_for=lambda: _workspace_llm_route_snapshot().snapshot,
    preflight_error_for=lambda: _CreationTaskTopicPreflightError,
    source_inputs_for=lambda: _source_inputs_use_case,
    response_for=lambda: _creation_task_text_response_adapter().creation_task_topic_response,
    automation_guard_for=lambda: _require_automation_task_open,
    draft_for=lambda: _script_draft_use_case,
    current_inputs_for=lambda: _require_current_script_inputs,
    draft_error_for=lambda: ScriptDraftRejected,
    http_error_for=lambda: HTTPException,
    adoption_for=lambda: _assistant_creation_adoption_scope,
    production_for=lambda: production_repo,
    reader_for=lambda: _content_identity_http_adapter().reader,
    failure_for=lambda: _topic_failure_response,
))


app.include_router(_task_workspace_http_router(('set_creation_task_visibility_route', 'get_creation_task_workspace')))




app.include_router(build_task_knowledge_adoption_router(
    access_for=lambda: _exact_edit_access,
    adoption_for=lambda: _assistant_creation_adoption_scope,
    production_for=lambda: production_repo,
    source_for=lambda: source_repository,
    http_error_for=lambda: HTTPException,
    response_for=lambda: TaskKnowledgeAdoptionResponse,
))


def _assistant_creation_adoption_scope(task_id: str, request: Request, turn_id: str, *, draft_only: bool = False):
    from app.creation.intent_use_case import CreationIntentUseCase
    workspace_id = current_workspace_id(request)
    owner = _content_identity_http_adapter().reader(request, workspace_id)[0]
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




def _script_context_use_case() -> ScriptContextUseCase:
    return ScriptContextUseCase(production_repo, _source_inputs_use_case())




def _task_script_http_router(route_names):
    return build_task_script_router(
        workspace_for=lambda: current_workspace_id,
        reader_for=lambda: _content_identity_http_adapter().reader,
        context_for=lambda: _script_context_use_case,
        draft_error_for=lambda: ScriptDraftRejected,
        http_error_for=lambda: HTTPException,
        reply_for=lambda: _verified_assistant_reply,
        current_inputs_for=lambda: _require_current_script_inputs,
        production_for=lambda: production_repo,
        uuid_for=lambda: uuid4,
        section_type_for=lambda: CreationTaskScriptSection,
        scripts_response_for=lambda: _creation_task_text_response_adapter().creation_task_scripts_response,
        adoption_for=lambda: _assistant_creation_adoption_scope,
        draft_for=lambda: _script_draft_use_case,
        script_response_for=lambda: CreationTaskScriptResponse,
        asdict_for=lambda: asdict,
        readable_for=lambda: _readable_creation_task,
        task_for=lambda: _creation_task_or_404,
        plan_for=lambda: _creation_plan_for_task_or_404,
        library_for=lambda: _script_library_use_case,
        route_names=route_names,
    )


app.include_router(_task_script_http_router(('get_assistant_script_context',)))


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
    return _validate_script_input_sources(workspace_id,plan,input_references,*_content_identity_http_adapter().reader(request,workspace_id))


def _validate_script_input_sources(workspace_id,plan,input_references,reader_id,local_diagnostic=False):
    try:
        return _source_inputs_use_case().validate_current(
            workspace_id, plan, input_references, reader_id, local_diagnostic)
    except _CreationTaskTopicPreflightError as exc:
        raise HTTPException(status_code=409, detail={
            "error": exc.reason, "message": exc.impact, "repair_action": exc.repair_action,
        }) from exc


app.include_router(_task_script_http_router(('transfer_assistant_reply_to_script',)))


def _script_draft_use_case(workspace_id, *, validate_inputs, content_reader=None):
    context = _script_context_use_case()
    case=ScriptDraftUseCase(production_repo,read_inputs=context.read_inputs,validate_inputs=validate_inputs,
        script_goal=context.goal,topic_origin=context.topic_origin,
        check_draft=lambda inputs,candidate:_creation_task_script_generator_for(_workspace_settings_for(workspace_id)).check_draft(inputs,candidate),
        record_service=lambda **facts: record_llm_service_verification(_workspace_settings_for(workspace_id),**facts),
        route_snapshot=_workspace_llm_route_snapshot().snapshot,
        record_adoption=lambda *args, **kwargs: _goal_service().record_script_adoption(*args, **kwargs))

    case.plan_for_task=_creation_plan_for_task_or_404
    case.topic_inputs=_source_inputs_use_case().prepare
    case.preflight_error_type=_CreationTaskTopicPreflightError
    case.topic_generator=lambda inputs:_creation_task_topic_generator_for(_workspace_settings_for(workspace_id)).generate(inputs)
    case.topic_response=_creation_task_text_response_adapter().creation_task_topic_response
    if content_reader is not None:
        _bind_script_library_use_case(case, content_reader)
    return case


app.include_router(_task_script_http_router(('save_assistant_script_draft',)))



app.include_router(build_task_assistant_history_router(
    workspace_for=lambda: current_workspace_id,
    task_for=lambda: _creation_task_or_404,
    response_for=lambda: CreationTaskAssistantHistoryResponse,
    production_for=lambda: production_repo,
))


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






def _assistant_stream_transport():
    return AssistantStreamTransport(
        conversations_for=lambda: conversation_repo,
        runtime_lock=_runtime_reload_lock, workers=_assistant_stream_workers,
        safe_text=_assistant_stream_safe_text,
        known_secrets_for=lambda workspace_id: (
            _workspace_settings_for(workspace_id).effective_llm_api_key,
            settings.tencentcloud_secret_id, settings.tencentcloud_secret_key),
        forget_control=lambda turn_id: forget_turn_control(turn_id), logger=logger,
    )


def _task_turn_http_adapter():
    return TaskTurnHTTPAdapter(
        conversations_for=lambda: conversation_repo,
        window_case_for=lambda workspace_id, member_id: _script_draft_use_case(workspace_id,
            validate_inputs=lambda ws, plan, refs: _validate_script_input_sources(ws, plan, refs, member_id)),
        workspace_for=current_workspace_id,
        read_task=_creation_task_or_404,
        read_task_record=lambda ws, task_id: production_repo.task_records.get_creation_task(ws, task_id),
        read_caller=_content_identity_http_adapter().reader,
        content_visible=_content_identity_http_adapter().visible,
        read_member=_content_identity_http_adapter().assistant_member,
        conversation_owner=_assistant_conversation_for_request,
        member_scope=_member_owned_content_scope,
        clean_text=_assistant_text_without_credentials,
        safe_text=_assistant_stream_safe_text,
        validate_reference=_validate_saved_assistant_reference,
        profile_snapshot=_assistant_profile_snapshot,
        knowledge_snapshot=_assistant_knowledge_snapshot,
        access_task=_exact_edit_access,
        read_subtitles=lambda ws, task_id: production_repo.subtitle_records.list_subtitle_tracks(ws, task_id),
        resolve_scope=_assistant_turn_scope,
        read_timeline=lambda ws, version_id: production_repo.timeline_records.get_timeline_version(ws, version_id),
        timeline_for_task=_timeline_version_or_404,
        clip_material=_assistant_task_clip_material_selection,
        material_snapshot=_assistant_material_selection_snapshot,
        settings_for=_workspace_settings_for,
        read_goal_plan=_creation_plan_for_task_or_404,
        goal_shutting_down=lambda: assistant_continuation_driver is not None and assistant_continuation_driver.stop_event.is_set(),
        runtime_for=lambda: _creation_runtime(),
        local_token=_local_member_token,
        request_actor=_request_permit_actor,
        require_actor=_require_permit_actor,
        native_handler=_ordinary_native_handler,
        run_task=lambda *args, **values: run_task_turn(*args, **values),
        register_control=lambda turn_id: register_turn_control(turn_id),
        forget_control=lambda turn_id: forget_turn_control(turn_id),
        stop_control=lambda turn_id: request_stop_turn(turn_id),
        stream_for=_assistant_stream_transport,
        applied_edit=_assistant_applied_edit_receipt,
        storyboard=_resolve_assistant_storyboard_regeneration,
        script_tool=_resolve_assistant_script_tool,
        related_media=_related_media_result,
        edit_request=_resolve_assistant_edit_request,
        media_binding=_media_result_binding,
        completed_media=_completed_media_result,
        read_media=_read_media_result,
        read_script=lambda ws, script_id: production_repo.script_records.get_creation_task_script(ws, script_id),
        bound_execution=_read_bound_execution_result,
        bind_observation=_bind_assistant_execution_observation,
        bind_execution=_bind_assistant_execution_result,
        read_proposal=lambda ws, proposal_id: production_repo.edit_records.get_edit_proposal(ws, proposal_id),
        read_intent=lambda ws, intent_id: production_repo.edit_records.get_edit_intent(ws, intent_id),
        render_edit_preview=_edit_preview_response,
        read_event=lambda ws, event_id: production_repo.execution_records.get_task_event(ws, event_id),
        list_events=lambda ws, task_id: production_repo.execution_records.list_task_events(ws, task_id),
        goal_for=_goal_service,
        bind_goal_scope=_bind_goal_business_scope,
        wake=_wake_assistant_continuations,
        read_quality=_read_creation_quality_continuation,
        read_window=_script_window_http_adapter().read_creation_script_window_request,
        read_segment=lambda ws, task_id, request_id, actor: _segment_production_use_case().read_request(ws, task_id, request_id, actor),
        read_restore=lambda ws, task_id, request_id, actor: _timeline_restore_use_case().read_request(ws, task_id, request_id, actor),
        generation_for=_script_generation_intent,
    )


app.include_router(build_task_turn_router(_task_turn_http_adapter))


def _task_turn_scope_use_case():
    return TaskTurnScopeUseCase(
        read_plan=lambda ws, plan_id: production_repo.plan_records.get_creation_plan(ws, plan_id),
        read_script_inputs=lambda ws, task_id: _script_context_use_case().read_inputs(ws, task_id),
        read_narration=lambda ws, key: production_repo.narration_artifacts.get_narration_artifact(ws, key),
        read_segments=lambda ws, task_id, script_id: production_repo.narration_records.list_narration_segments(ws, task_id, script_id),
        read_montage=lambda ws, task_id: production_repo.get_creation_task_montage_plan(ws, task_id),
        read_version=lambda ws, version_id: production_repo.timeline_records.get_timeline_version(ws, version_id),
        read_subtitles=lambda ws, task, **values: _metadata_edit_use_case().current_subtitle_tracks(ws, task, **values),
        read_covers=lambda ws, task_id, **values: production_repo.cover_records.list_cover_assets(ws, task_id, **values),
    )


def _assistant_turn_scope(
    workspace_id: str, task: CreationTask, payload: CreationTaskAssistantTurnRequest,
) -> tuple[str, list[str]]:
    try:
        return _task_turn_scope_use_case().resolve(workspace_id, task, TaskTurnSelection(
            context_stage=payload.context_stage, selected_object_refs=payload.selected_object_refs,
            playhead_ms=payload.playhead_ms))
    except TaskTurnScopeRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc




def _assistant_result_binding(task_id, request, turn_id, details):
    workspace_id = current_workspace_id(request)
    member_id = _content_identity_http_adapter().assistant_member(request, workspace_id)
    try:
        return conversation_repo.task_result_source(turn_id, workspace_id, member_id, task_id,
            input_id=details.get("input_id"), input_turn_id=details.get("input_turn_id"))
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _assistant_edit_result_binding():
    return AssistantEditResultBinding(production_repo, conversation_repo)


def _resolve_assistant_edit_request(task_id, request, turn_id, details):
    binding = _assistant_edit_result_binding()
    try:
        reference = binding.request_reference(task_id, turn_id, details)
        try:
            task = _creation_task_or_404(current_workspace_id(request), task_id)
            member_id = _content_identity_http_adapter().assistant_member(request, task.workspace_id)
            actual = _exact_preview_use_case().read_request_receipt(
                task, member_id, str(reference.request_id), reference.phase)
        except (HTTPException, GoalProjectionRejected, EditPreviewRejected) as exc:
            if exc.status_code != 404:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
            actual = binding.missing_result(task_id, turn_id, reference, details)
        locator = binding.locator(task_id, reference, actual)
        workspace_id = current_workspace_id(request)
        member_id = _content_identity_http_adapter().assistant_member(request, workspace_id)
        prepared = binding.prepare_binding(workspace_id, task_id, member_id, turn_id, locator, details)
        actor = None
        if prepared.source is None:
            actor = (_request_permit_actor(request, workspace_id) if auth_context.enabled
                     else CreationExecutionActor(workspace_id, member_id))
        return binding.record_locator(prepared, actor)
    except EditResultRejected as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc








def _resolve_assistant_script_tool(task_id, request, turn_id, details):
    workspace_id = current_workspace_id(request)
    member_id = _content_identity_http_adapter().assistant_member(request, workspace_id)
    actor = _require_permit_actor(request, workspace_id)
    binding = AssistantScriptToolResultBinding(production_repo, conversation_repo, _media_script_tools(),
        verify_reader=lambda actor: _assistant_execution_binding().require_actor(actor))
    try:
        return binding.resolve(workspace_id, task_id, member_id, turn_id, actor, details)
    except ExecutionBindingRejected as exc:
        raise _execution_binding_http(exc) from exc
    except ScriptToolReceiptRejected as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    except ScriptToolLookupRejected as exc:
        _script_tool_error(exc.reason)








def _assistant_applied_edit_receipt(task_id, request, turn_id, details):
    workspace_id = current_workspace_id(request)
    edit_request = _resolve_assistant_edit_request(task_id, request, turn_id, details)
    try:
        return _assistant_edit_result_binding().require_applied(workspace_id, task_id, turn_id, edit_request)
    except EditResultRejected as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc


def _storyboard_result_binding():
    return AssistantStoryboardResultBinding(
        result_source=conversation_repo.task_result_source,
        current_source=conversation_repo.task_turn_source,
        read_original=lambda ws, task_id, request_id, actor: _creation_execution_control().read_storyboard_regeneration(ws, task_id, request_id, actor),
        read_start_event=production_repo.execution_records.get_task_event,
        read_task=production_repo.task_records.get_creation_task,
        read_version=production_repo.timeline_records.get_timeline_version,
        edited_execution_version=lambda *args, **values: _assistant_execution_binding().edited_execution_version(*args, **values),
    )


def _resolve_assistant_storyboard_regeneration(task_id, request, turn_id, details):
    workspace_id = current_workspace_id(request)
    member_id = _content_identity_http_adapter().assistant_member(request, workspace_id)
    binding = _storyboard_result_binding()
    try:
        origin = binding.source(workspace_id, task_id, member_id, turn_id, details)
        member_id = _content_identity_http_adapter().assistant_member(request, workspace_id)
        reference = binding.reference(workspace_id, task_id, member_id, turn_id, details, origin)
        _exact_edit_access(task_id, request)
        actor = _require_permit_actor(request, workspace_id)
        actual = binding.read(reference, actor)
        if isinstance(actual, UnknownStoryboardReference):
            try:
                return AssistantStoryboardRegenerationFailure.model_validate({
                    "kind": reference.kind, "action": reference.action, "request_id": reference.request_id, "query_url": reference.query_url,
                    "failure": {key: _assistant_stream_safe_text(details["failure"][key], workspace_id)[:500]
                        for key in ("reason", "repair_action")}})
            except (KeyError, ValidationError, TypeError) as invalid:
                raise HTTPException(status_code=502, detail="故事板未知结果定位不可核验") from invalid
        pending = binding.check_original(reference, actual)
        if pending is not None:
            try:
                binding.check_successor(pending, input_id=request.headers.get('X-Assistant-Input-ID') or None)
            except ExecutionBindingRejected as exc:
                raise _execution_binding_http(exc) from exc
            except (GoalProjectionRejected, EditPreviewRejected) as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return AssistantStoryboardRegenerationReceipt(kind="storyboard_regeneration_receipt", action=reference.action, receipt=actual)
    except StoryboardResultRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc




app.include_router(_task_script_http_router(('get_creation_task_scripts',)))




def _script_window_http_adapter():
    return ScriptWindowHttpAdapter(
        exact_edit_access=lambda task_id, request, **values: _exact_edit_access(task_id, request, **values),
        adoption_scope=lambda task_id, request, turn_id, **values: _assistant_creation_adoption_scope(task_id, request, turn_id, **values),
        draft_use_case=lambda workspace_id, **values: _script_draft_use_case(workspace_id, **values),
        current_script_inputs=lambda request, ws, plan, refs: _require_current_script_inputs(request, ws, plan, refs),
        content_reader=lambda request, ws: _content_identity_http_adapter().reader(request, ws),
    )










app.include_router(build_script_sections_router(
    readable_task=_readable_creation_task,
    read_sections=lambda *args, **kwargs: production_repo.script_records.read_creation_task_script_sections(*args, **kwargs),
    patch_sections=_script_window_http_adapter().patch_creation_script_sections,
    start_window=_script_window_http_adapter().start_creation_script_window,
    confirm_saved=_script_window_http_adapter().confirm_saved_creation_script,
    read_request=_script_window_http_adapter().read_creation_script_window_request,
    read_sources=_script_window_http_adapter().read_creation_script_edit_sources,
))


def _script_generation_intent(workspace_id):
    return ScriptGenerationIntent(production_repo, conversation_repo,
        source_inputs=_source_inputs_use_case().prepare, route_snapshot=_workspace_llm_route_snapshot().snapshot(workspace_id),
        verify_identity=_verify_creation_identity)


def _generation_script_case(workspace_id, member_id, intent):
    case = _script_draft_use_case(workspace_id,
        validate_inputs=lambda ws, plan, refs: _validate_script_input_sources(ws, plan, refs, member_id),
        content_reader=lambda ws: (member_id, False))
    case.read_generation_request = intent.read_generation_request
    case.read_generation_result = intent.read
    case.bind_generation_result = intent.bind_generation_result
    return case


def _accept_script_generation(request, task_id, request_id):
    task = _readable_creation_task(request, task_id)
    _require_automation_task_open(request, task.workspace_id, task_id)
    actor = _require_permit_actor(request, task.workspace_id)
    case = _script_generation_intent(task.workspace_id)
    record, created = case.start(task.workspace_id, task_id, request_id, actor)
    dispatch = (task.workspace_id, task_id, request_id, actor.user_id or actor.member_id) if created else None
    return case.public_projection(record), dispatch


def _read_script_generation(request, task_id, request_id):
    task = _readable_creation_task(request, task_id)
    member_id = _content_identity_http_adapter().assistant_member(request, task.workspace_id)
    case = _script_generation_intent(task.workspace_id)
    return case.public_projection(case.read(task.workspace_id, task_id, request_id, member_id))


def _execute_script_generation(dispatch):
    workspace_id, task_id, request_id, member_id = dispatch
    case = _script_generation_intent(workspace_id)
    return case.execute(workspace_id, task_id, request_id, member_id,
        run_agent=lambda record: _run_script_generation_agent(workspace_id, member_id, case, record),
        script_case=_generation_script_case(workspace_id, member_id, case))


def _run_script_generation_agent(workspace_id, member_id, case, record):
    from app.assistant.script_generation_runner import run_generation_agent
    def on_event(event):
        if event.get("type") == "context_compaction":
            safe = _safe_compaction_event(event)
        elif event.get("type") in {"tool_start", "tool_end"}:
            safe = {key: event[key] for key in ("type", "tool_id", "label", "ok", "summary") if key in event}
            for key in ("label", "summary"):
                if key in safe:
                    safe[key] = _assistant_stream_safe_text(str(safe[key]), workspace_id)
            if isinstance(event.get("failure"), dict):
                safe["failure"] = _safe_business_failure(event["failure"])
        else:
            return
        if safe is not None:
            conversation_repo.append_event(record["turn_id"], safe)
    return run_generation_agent(_workspace_settings_for(workspace_id), workspace_id=workspace_id,
        member_id=member_id, record=record, business=_task_business(generation_case=case),
        read_request=lambda: case.read(workspace_id, record["task_id"], record["request_id"], member_id),
        on_event=on_event)


app.include_router(build_script_generation_router(
    accept=_accept_script_generation, read=_read_script_generation, execute=_execute_script_generation))


app.include_router(_task_script_http_router(('confirm_creation_task_script',)))




app.include_router(build_task_narration_router(
    access_for=lambda: _exact_edit_access,
    adoption_for=lambda: _assistant_creation_adoption_scope,
    production_for=lambda: production_repo,
    execution_for=lambda: creation_execution,
    actor_for=lambda: _request_permit_actor,
    control_for=lambda: _creation_execution_control,
    runtime_for=lambda: device_runtime,
    http_error_for=lambda: HTTPException,
    authority_error_for=lambda: MediaAuthorityError,
    failure_for=lambda: _topic_failure_response,
    blocked_for=lambda: RecoveryBlocked,
    selection_response_for=lambda: CreationTaskNarrationVoiceSelectionResponse,
    workspace_for=lambda: current_workspace_id,
    readable_for=lambda: _readable_creation_task,
    path_for=lambda: Path,
    file_response_for=lambda: FileResponse,
    ingress_for=lambda: _submit_audio_ingress,
))


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






def _task_person_render_http_router(route_names):
    return build_task_person_render_router(
        workspace_for=lambda: current_workspace_id,
        task_for=lambda: _creation_task_or_404,
        production_for=lambda: production_repo,
        list_response_for=lambda: CreationTaskPersonRendersResponse,
        render_response_for=lambda: _creation_task_response_adapter().person_render_response,
        generate_for=lambda: _generate_creation_task_person_render_record,
        readable_for=lambda: _readable_creation_task,
        http_error_for=lambda: HTTPException,
        media_path_for=lambda: _person_render_media_path,
        file_response_for=lambda: FileResponse,
        route_names=route_names,
    )


app.include_router(_task_person_render_http_router(('list_creation_task_person_renders',)))


def _generate_creation_task_person_render_record(workspace_id,task_id,payload,*,regeneration_token=None,grant_context=None):
    try:
        return _person_stage_use_case().generate_record(workspace_id,task_id,payload,regeneration_token=regeneration_token,grant_context=grant_context)
    except PersonStageRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc



app.include_router(_task_person_render_http_router(('generate_creation_task_person_render', 'get_creation_task_person_render_media')))




app.include_router(build_storyboard_regeneration_router(
    workspace_for=lambda: current_workspace_id,
    access_for=lambda: _exact_edit_access,
    adoption_for=lambda: _assistant_creation_adoption_scope,
    actor_for=lambda: _require_permit_actor,
    runtime_for=lambda: _creation_runtime,
    control_for=lambda: _creation_execution_control,
    control_error_for=lambda: ExecutionControlRejected,
    http_error_for=lambda: HTTPException,
))


def _create_creation_task_montage_plan_for_workspace(workspace_id: str, task_id: str,
    *, grant_context: CreationStageGrantContext | None = None) -> CreationTaskMontagePlanResponse:
    try:
        montage = _montage_stage_use_case().generate(workspace_id, task_id, grant_context=grant_context)
    except MontageStageRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return _creation_task_response_adapter().creation_task_montage_response(montage)


def _creation_task_montage_candidates(workspace_id,plan,clip,*,automation_only=False,viewer_user_id=None,local_diagnostic=False):
    return _metadata_edit_use_case().montage_candidates(workspace_id,plan,clip,automation_only=automation_only,viewer_user_id=viewer_user_id,local_diagnostic=local_diagnostic)




def _task_montage_control_http_router(route_names):
    return build_task_montage_control_router(
        workspace_for=lambda: current_workspace_id,
        task_for=lambda: _creation_task_or_404,
        plan_from_for=lambda: creation_task_montage_plan_from_payload,
        production_for=lambda: production_repo,
        http_error_for=lambda: HTTPException,
        candidates_for=lambda: _creation_task_montage_candidates,
        reader_for=lambda: _content_identity_http_adapter().reader,
        save_for=lambda: _save_direct_metadata_edit,
        edit_request_for=lambda: CreationTaskExactEditRequest,
        fields_for=lambda: EXACT_EDIT_FIELDS,
        route_names=route_names,
    )


app.include_router(_task_montage_control_http_router(('get_creation_task_montage_clip_candidates',)))


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


def _narration_runtime_facts(workspace_id, task_id, *, repository=None):
    from app.device_resource_configuration import narration_execution_facts
    repo = production_repo if repository is None else repository
    return narration_execution_facts(settings=settings, model_catalog=model_catalog,
        generator=narration_generator, database_path=repo.database_path,
        workspace_id=workspace_id, task_id=task_id)



def _segment_production_use_case(repository=None):
    from app.creation.segment_production_use_case import SegmentProductionUseCase
    repo = production_repo if repository is None else repository
    return SegmentProductionUseCase(repo,narration_stage=_narration_stage_use_case(repo),
        renderer=montage_renderer,artifact_store=artifact_store,
        output_root=settings.effective_temporary_root_path,source_repository=source_repository,
        narration_runtime_facts=lambda workspace_id,task_id: _narration_runtime_facts(
            workspace_id,task_id,repository=repo))


def _native_task_operations_case(repository=None,*,budget=_RESOURCE_BUDGET_UNSET,authorizations=None):
    from app.creation.native_task_operations import NativeTaskOperations
    repo = production_repo if repository is None else repository
    return NativeTaskOperations(repo,artifact_store,repo.database_path.parent/'native-task-workspaces',
        budget=_device_operation_budget("native_command") if budget is _RESOURCE_BUDGET_UNSET else budget,
        authorizations=authorizations if authorizations is not None else _task_authorizations_for_goal(),
        python_path=settings.model_runtime_python_path)


def _ordinary_native_handler(actor,binding):
    from app.creation.native_task_operations import NativeOperationHandler
    case = _native_task_operations_case()
    initialized = False
    def verify(source,conn):
        nonlocal initialized
        if any(source.get(key)!=binding[key] for key in ('workspace_id','member_id','task_id','conversation_id','turn_id')):
            raise PermissionError('原生工具不能切换原任务、成员或对话')
        if actor.device_id is None and find_member(local_members_service,actor.member_id) is None:
            raise PermissionError('当前本地成员已失效，未执行原生操作')
        current = (conversation_repo._task_turn_source(conn,source['turn_id'],source['workspace_id'],source['member_id'],source['task_id'])
            if conn is not None else conversation_repo.task_turn_source(source['turn_id'],source['workspace_id'],source['member_id'],source['task_id']))
        if (current['conversation_id'],current.get('input_id'))!=(source['conversation_id'],source.get('input_id')) or not source.get('session_id'):
            raise PermissionError('原生工具不是当前已消费输入与实际SDK会话')
        authorization = case.authorizations
        if conn is None and not initialized:
            task = production_repo.task_records.get_creation_task(source['workspace_id'],source['task_id'])
            authorization.ensure(task,actor,interactive=True)
            initialized = True
        if authorization.status(source['workspace_id'],source['task_id'],identity=authorization.identity(actor),
                operation='task_goal',connection=conn,check_scope=False)!='valid':
            raise PermissionError('原任务授权已到期、撤销或超出原范围，未执行新操作')
        return {**current,'task_id':source['task_id'],'assistant_turn_id':source['turn_id'],
            'input_id':current.get('input_id') or source['turn_id'],'session_id':source['session_id'],'sdk_round':source.get('sdk_round',0)}
    return NativeOperationHandler(case,actor,verify,_creation_runtime)




def _native_task_read_http_router(route_names):
    return build_native_task_read_router(
        workspace_for=lambda: current_workspace_id,
        actor_for=lambda: _request_permit_actor,
        http_error_for=lambda: HTTPException,
        case_for=lambda: _native_task_operations_case,
        file_response_for=lambda: FileResponse,
        route_names=route_names,
    )


app.include_router(_native_task_read_http_router(("creation_task_native_operations",)))


app.include_router(_native_task_read_http_router(("creation_task_native_artifact",)))


def _local_revision_origin(task_id,request,payload):
    """Adapt an existing consumed assistant turn; identity never comes from model arguments."""
    ws = current_workspace_id(request)
    actor = _require_permit_actor(request,ws)
    turn_id = payload.get('assistant_turn_id')
    if not isinstance(turn_id,str) or not turn_id:
        raise HTTPException(status_code=409,detail='修改判断必须绑定正在运行的可信助手回合')
    origin = _assistant_creation_adoption_scope(task_id,request,turn_id,draft_only=True)
    input_id = origin.get('input_id') or turn_id
    if payload.get('input_id') != input_id:
        raise HTTPException(status_code=409,detail='修改判断没有绑定当前原输入')
    return actor,{**origin,'assistant_turn_id':turn_id,'input_id':input_id}




def _assistant_semantic_receipt_http_router(route_names):
    return build_assistant_semantic_receipt_router(
        origin_for=lambda: _local_revision_origin,
        conversations_for=lambda: conversation_repo,
        production_for=lambda: production_repo,
        http_error_for=lambda: HTTPException,
        route_names=route_names,
    )


app.include_router(_assistant_semantic_receipt_http_router(("creation_task_input_understanding",)))


app.include_router(_assistant_semantic_receipt_http_router(("creation_task_narration_semantic_review",)))




def _task_segment_http_router(route_names):
    return build_task_segment_router(
        workspace_for=lambda: current_workspace_id,
        case_for=lambda: _segment_production_use_case,
        actor_for=lambda: _require_permit_actor,
        http_error_for=lambda: HTTPException,
        automation_guard_for=lambda: _require_automation_task_open,
        origin_for=lambda: _local_revision_origin,
        production_for=lambda: production_repo,
        conversations_for=lambda: conversation_repo,
        runtime_for=lambda: _creation_runtime,
        file_response_for=lambda: FileResponse,
        route_names=route_names,
    )


app.include_router(_task_segment_http_router(("creation_task_segments",)))


app.include_router(_task_segment_http_router(("creation_task_segment_context",)))


app.include_router(_task_segment_http_router(("creation_task_segment_sources",)))


app.include_router(_task_segment_http_router(("revise_creation_task_segment",)))


app.include_router(_task_segment_http_router(("creation_task_segment_request",)))


app.include_router(_task_segment_http_router(("resume_creation_task_segment",)))


app.include_router(_task_segment_http_router(("creation_task_segment_video",)))






def _task_budget_http_router(route_names):
    return build_task_budget_router(
        workspace_for=lambda: current_workspace_id,
        production_for=lambda: production_repo,
        actor_for=lambda: _require_permit_actor,
        http_error_for=lambda: HTTPException,
        automation_guard_for=lambda: _require_automation_task_open,
        control_for=lambda: _creation_execution_control,
        runtime_for=lambda: _creation_runtime,
        control_error_for=lambda: ExecutionControlRejected,
        route_names=route_names,
    )


app.include_router(_task_budget_http_router(("creation_task_budget",)))


app.include_router(_task_budget_http_router(("resume_creation_task_budget",)))


def _cover_use_case(repository=None, *, budget=_RESOURCE_BUDGET_UNSET):
    repo = production_repo if repository is None else repository
    return CoverUseCase(repo, artifact_store, _picture_preview_components(repo)[1],
        verify_identity=_verify_creation_identity, output_root=settings.video_output_path,
        budget=_device_operation_budget("cover") if budget is _RESOURCE_BUDGET_UNSET else budget,
        on_settled=_wake_assistant_continuations)


def _cover_confirmation_http_adapter():
    return CoverConfirmationHttpAdapter(
        repository_for=lambda: production_repo,
        require_automation_task_open=lambda request, workspace_id, task_id: _require_automation_task_open(request, workspace_id, task_id),
        require_permit_actor=lambda request, workspace_id: _require_permit_actor(request, workspace_id),
        cover_use_case_for=lambda: _cover_use_case(),
        exact_edit_source_facts=lambda task, payload, request: _exact_edit_source_facts(task, payload, request),
        creation_runtime_for=lambda: _creation_runtime(),
        montage_response=lambda plan: _creation_task_response_adapter().creation_task_montage_response(plan),
        topic_failure_response=lambda *, status_code, error, reason, impact, repair_action, retryable: _topic_failure_response(
            status_code=status_code, error=error, reason=reason, impact=impact, repair_action=repair_action, retryable=retryable),
    )




def _metadata_edit_use_case():
    return _picture_preview_components()[1]


def _linked_timeline_use_case():
    return LinkedTimelineEditUseCase(production_repo, _metadata_edit_use_case())


def _linked_timeline_values(payload):
    try:
        return _exact_preview_use_case().linked_values(payload)
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc


def _task_edit_command_http_adapter():
    return TaskEditCommandHttpAdapter(
        repo_for=lambda: production_repo,
        content_reader=lambda request, workspace_id: _content_identity_http_adapter().reader(request, workspace_id),
        assistant_member=lambda request, workspace_id: _content_identity_http_adapter().assistant_member(request, workspace_id),
        exact_case_for=lambda: _exact_preview_use_case(),
    )




def _task_edit_confirmation_http_adapter():
    return TaskEditConfirmationHttpAdapter(
        repo_for=lambda: production_repo,
        workspace=lambda request: current_workspace_id(request),
        task404=lambda workspace_id, task_id: _creation_task_or_404(workspace_id, task_id),
        cover_confirmation=lambda task, proposal, request: _cover_confirmation_http_adapter().confirm_cover_response(task, proposal, request),
        metadata_confirmations_for=lambda: _metadata_edit_confirmation_http_adapter(),
        source_facts=lambda task, action, request: _exact_edit_source_facts(task, action, request),
        content_reader=lambda request, workspace_id: _content_identity_http_adapter().reader(request, workspace_id),
        member_id=lambda request, workspace_id: _content_identity_http_adapter().assistant_member(request, workspace_id),
        task_open=lambda request, workspace_id, task_id: _require_automation_task_open(request, workspace_id, task_id),
        auth_enabled=lambda: auth_context.enabled,
        request_actor=lambda request, workspace_id: _request_permit_actor(request, workspace_id),
        exact_case_for=lambda: _exact_preview_use_case(),
        metadata_response=lambda outcome: _metadata_edit_response(outcome),
        topic_failure=lambda **values: _topic_failure_response(**values),
    )


def _metadata_edit_confirmation_http_adapter():
    return MetadataEditConfirmationHttpAdapter(
        linked_case_for=lambda: _linked_timeline_use_case(),
        subtitle_case_for=lambda: _subtitle_edit_use_case(),
        compound_case_for=lambda: _compound_edit_use_case(),
        linked_command=lambda task, proposal, request, payload: _task_edit_command_http_adapter().linked_timeline_apply_command(task, proposal, request, payload),
        subtitle_command=lambda task, proposal, request: _task_edit_command_http_adapter().subtitle_proposal_command(task, proposal, request),
        compound_command=lambda task, proposal, request: _task_edit_command_http_adapter().compound_proposal_command(task, proposal, request),
        metadata_response=lambda outcome: _metadata_edit_response(outcome),
        save_receipt=lambda task, event_id, response: _task_edit_receipt_http_adapter().save_edit_request_result(task, event_id, response),
        repo_for=lambda: production_repo,
        wake=lambda: _wake_assistant_continuations(),
    )


def _metadata_edit_projection(outcome):
    if isinstance(outcome, MetadataEditReplay):
        if outcome.recorded_projection is not None:
            recorded = outcome.recorded_projection
            return recorded['status_code'], recorded['response']
        outcome = outcome.result
    value = CreationTaskEditConfirmResponse(operation=asdict(outcome.operation),
        restore_request=outcome.restore_request,
        execution_readiness=outcome.execution_readiness,
        montage=_creation_task_response_adapter().creation_task_montage_response(outcome.montage) if outcome.montage is not None else None,
        narration=_creation_task_response_adapter().creation_task_narration_response(outcome.task, outcome.narration)
            if outcome.narration is not None else None)

    return 200, value.model_dump(mode='json')


def _metadata_edit_response(outcome):
    status, value = _metadata_edit_projection(outcome)
    if isinstance(outcome, MetadataEditReplay) and outcome.recorded_projection is not None:
        return JSONResponse(status_code=status, content=value)
    return CreationTaskEditConfirmResponse.model_validate(value)




def _preview_metadata_settings(task, request, operation_type, values, base_version_id):
    reader, diagnostic = _content_identity_http_adapter().reader(request, task.workspace_id)
    source = EXACT_EDIT_SOURCE.get()
    if source is not None:
        reader, diagnostic = _content_identity_http_adapter().assistant_member(request, task.workspace_id), False
    try:
        return _edit_preview_response(*_metadata_edit_use_case().preview_metadata_settings(task, operation_type, values, base_version_id,
            identity={'member_id':reader,'local_diagnostic':diagnostic}, source=source))
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _save_direct_metadata_edit(task_id, request, payload):
    if payload.assistant_turn_id is not None and payload.request_id is None:
        raise HTTPException(status_code=422, detail='助手直接编辑需要原请求身份')
    task, member_id = _exact_edit_access(task_id, request, write=True)
    actor = _request_permit_actor(request, task.workspace_id) if auth_context.enabled else CreationExecutionActor(task.workspace_id, member_id)
    try:
        result = _exact_preview_use_case().apply(task, actor, payload,
            identity={'member_id': member_id, 'local_diagnostic': False},
            expected_input_id=request.headers.get('X-Assistant-Input-ID'))
        if result.changed:
            _wake_assistant_continuations()
        return CreationTaskEditConfirmResponse.model_validate(result.value)
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _logical_montage_output_refs(plan: CreationTaskMontagePlan) -> list[str]:
    try:
        return metadata_logical_montage_output_refs(plan)
    except LegacyMontageReference as exc:
        raise _topic_failure_response(**exc.details) from exc


def _current_narration_snapshot(workspace_id: str, task: CreationTask) -> dict[str, object]:
    return _metadata_edit_use_case().current_narration_snapshot(workspace_id, task)


def _ensure_montage_base_version(
    workspace_id: str,
    task: CreationTask,
    plan: CreationTaskMontagePlan,
) -> TimelineVersion:
    return _metadata_edit_use_case().ensure_montage_base(workspace_id, task, plan)


app.include_router(_task_edit_http_router(('initialize_creation_task_timeline',)))


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


def _bind_assistant_edit_instruction(request,task,payload):
    if payload.assistant_turn_id is None:
        return payload
    turn=conversation_repo.get_turn(str(payload.assistant_turn_id))
    if turn is None:
        raise HTTPException(status_code=404,detail='本轮对话不存在或不可访问。')
    workspace_id,member_id,_conversation=_assistant_conversation_for_request(request,turn['conversation_id'])
    try:
        origin=conversation_repo.task_turn_source(turn['id'],workspace_id,member_id,task.id)
        _require_assistant_input_origin(request, {**origin, 'assistant_turn_id': turn['id']})
        return _command_preview_use_case().bind_after_access(task,payload,turn,workspace_id,member_id)
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc





app.include_router(_task_edit_http_router(('preview_creation_task_edit',)))







app.include_router(_task_edit_http_router(('get_creation_task_edit_preview',)))


app.include_router(_task_edit_http_router(('get_creation_task_edit_controls',)))




app.include_router(_task_edit_http_router(('reject_creation_task_edit_proposal',)))






app.include_router(_task_edit_http_router(('get_creation_task_edit_result',)))


app.include_router(_task_edit_http_router(('confirm_creation_task_edit',)))





app.include_router(build_task_capability_router(
    access_for=lambda: _exact_edit_access,
    edit_facts_for=lambda: _task_edit_capability_response_adapter().facts,
    execution_facts_for=lambda: _task_execution_capability_facts,
))


def _task_execution_capability_facts(task):
    from app.creation.execution_capabilities import execution_capability_facts
    from app.creation.execution_control import task_execution_requirements

    # Reading installed facts must not assemble/start the runtime or sample the host.
    runtime = device_runtime
    observer = runtime.host_observer if runtime is not None else None
    facts = execution_capability_facts(
        budget=observer.budget if observer is not None else None,
        registrations=runtime.registrations if runtime is not None else {},
    )
    facts['task_execution'] = task_execution_requirements(task,
        production_repo.plan_records.get_creation_plan(task.workspace_id, task.plan_id))
    return facts



def _edit_capability_read_use_case():
    return EditCapabilityReadUseCase(
        read_montage=lambda ws, task_id: production_repo.get_creation_task_montage_plan(ws, task_id),
        read_subtitles=lambda ws, task_id, **values: production_repo.subtitle_records.list_subtitle_tracks(ws, task_id, **values),
        read_version=lambda ws, version_id: production_repo.timeline_records.get_timeline_version(ws, version_id),
    )


def _task_edit_capability_response_adapter():
    return TaskEditCapabilityResponseAdapter(reader_for=lambda: _edit_capability_read_use_case())


app.include_router(_task_edit_http_router(('preview_creation_task_exact_edit',)))


app.include_router(_task_edit_http_router(('apply_creation_task_exact_edit',)))


app.include_router(_task_edit_http_router(('preview_compound_selection',)))


def _compound_edit_use_case(repository=None):
    repo = repository if repository is not None else production_repo
    _audio, metadata, renderer = _picture_preview_components(repo)
    return CompoundEditUseCase(repo, metadata, picture_renderer=renderer)


def _edit_control_use_case(repository=None):
    from app.creation.editing.control_use_case import EditControlUseCase
    repo = repository if repository is not None else production_repo
    return EditControlUseCase(repo, conversations=conversation_repo, compound=_compound_edit_use_case(repo),
        preview_response=_edit_preview_response, wake=_wake_assistant_continuations)











def _graphic_overlay_candidate(plan,payload):
    return _metadata_edit_use_case().graphic_overlay_candidate(plan,payload)

def _subtitle_edit_use_case():
    return SubtitleEditUseCase(production_repo, _metadata_edit_use_case())


def _subtitle_values(payload):
    try:
        return subtitle_values(payload)
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc






def _trim_clip_source_url(workspace_id,task_id,clip,cue):
    return _metadata_edit_use_case().trim_clip_source_url(workspace_id,task_id,clip,cue)




def _timeline_restore_http_router(route_names):
    return build_timeline_restore_router(
        workspace_for=lambda: current_workspace_id,
        access_for=lambda: _exact_edit_access,
        restore_for=lambda: _timeline_restore_use_case,
        command_type_for=lambda: TimelineRestoreCommand,
        actor_type_for=lambda: CreationExecutionActor,
        unavailable_type_for=lambda: TimelineRestoreUnavailable,
        legacy_type_for=lambda: LegacyMontageReference,
        topic_failure_for=lambda: _topic_failure_response,
        invalid_type_for=lambda: InvalidMontage,
        http_error_for=lambda: HTTPException,
        blocked_type_for=lambda: RecoveryBlocked,
        response_for=lambda: _metadata_edit_response,
        route_names=route_names,
    )


app.include_router(_timeline_restore_http_router(('undo_creation_task_timeline', 'redo_creation_task_timeline')))


def _timeline_restore_use_case():
    return TimelineRestoreUseCase(production_repo, _metadata_edit_use_case(), conversations=conversation_repo,
        qualify_output=lambda ws, task_id, version: _creation_render_is_valid(ws, task_id, verify_media=False, timeline_version=version))


app.include_router(_timeline_restore_http_router(('get_creation_task_timeline_restore_request',)))


def _apply_creation_task_montage_clip_update(workspace_id,task_id,plan,clip_id,payload,*,request):
    reader,diagnostic=_content_identity_http_adapter().reader(request,workspace_id)
    try:
        return _metadata_edit_use_case().apply_clip_update(workspace_id,task_id,plan,clip_id,payload,
        reader_identity={'member_id':reader,'local_diagnostic':diagnostic})
    except EditPreviewRejected as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail) from exc


app.include_router(_task_montage_control_http_router(('update_creation_task_montage_clip', 'update_creation_task_subtitle_cue', 'render_creation_task_montage')))








def _render_creation_task_montage_for_workspace(
    workspace_id: str, task_id: str, *, render_context: CpuRenderGrantContext | None = None,
) -> CreationTaskMontagePlanResponse:
    try:
        plan = _render_stage_use_case().render(workspace_id, task_id, render_context=render_context)
    except RenderRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return _creation_task_response_adapter().creation_task_montage_response(plan)


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
    user_id, _ = _content_identity_http_adapter().reader(request, workspace_id)
    if user_id is None:
        raise HTTPException(status_code=401, detail="请使用可信成员身份检查成片")
    if (_local_member_token(request) and workspace_id != LEGACY_WORKSPACE_ID
            and getattr(request.state, "principal", None) is None):
        raise HTTPException(status_code=403, detail="本机成员与工作区不匹配")
    task = _creation_task_or_404(workspace_id, task_id)
    if not _content_identity_http_adapter().visible(request, workspace_id, task.visibility, task.owner_user_id):
        raise HTTPException(status_code=404, detail="creation task not found")
    return task


def _quality_check_source(workspace_id: str, task_id: str):
    try:
        return _output_observation_use_case().quality_source_metadata(workspace_id, task_id)
    except OutputObservationRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _quality_preparation_policy():
    profile = _device_resource_profile()
    return {"budget": profile.budget.quality,
            "profile": {"profile_id": profile.profile_id, "revision": profile.revision,
                        "digest": profile.digest, "source_kind": profile.source_kind}}


def _quality_inspection_use_case(repository=None, *, task_authorizations=None, budget=_RESOURCE_BUDGET_UNSET):
    error = None
    if budget is _RESOURCE_BUDGET_UNSET:
        try:
            budget = _device_host_budget().quality
        except HostBudgetUncalibrated as exc:
            budget, error = None, str(exc)
    repo = production_repo if repository is None else repository
    if task_authorizations is None and device_runtime is not None:
        task_authorizations = device_runtime.task_authorizations
    return QualityInspectionUseCase(repo, quality_check_repo, _output_observation_use_case(repo),
        budget=budget, budget_error=error, verify_identity=_verify_creation_identity,
        task_authorizations=task_authorizations,
        current_policy=_quality_preparation_policy,
        temporary_root=settings.effective_temporary_root_path / "quality-checks")


def _creation_task_quality_check_response(workspace_id: str, task_id: str) -> CreationTaskQualityCheckResponse:
    _creation_task_or_404(workspace_id, task_id)
    return CreationTaskQualityCheckResponse.model_validate(_quality_inspection_use_case().projection(workspace_id, task_id))


def _start_creation_task_quality_check(workspace_id: str, task_id: str, *,
                                     actor: CreationExecutionActor, force: bool = False) -> None:
    _quality_inspection_use_case().start(device_runtime, actor, workspace_id, task_id, force=force)




def _quality_http_router(route_names, *, read_continuation=None):
    return build_quality_router(
        workspace_for=lambda: current_workspace_id,
        access_for=lambda: _quality_check_task_access,
        request_actor_for=lambda: _request_permit_actor,
        http_error_for=lambda: HTTPException,
        account_error_for=lambda: DeviceAccountError,
        response_type_for=lambda: CreationTaskQualityCheckResponse,
        application_for=lambda: _quality_application,
        reader_for=lambda: _content_identity_http_adapter().reader,
        start_for=lambda: _start_creation_task_quality_check,
        require_actor_for=lambda: _require_permit_actor,
        projection_for=lambda: _creation_task_quality_check_response,
        read_continuation=read_continuation, route_names=route_names,
    )


app.include_router(_quality_http_router(("get_creation_task_quality_check",)))


def recover_interrupted_quality_checks(runtime) -> None:
    _quality_inspection_use_case().recover(runtime.scheduler if runtime is not None else None)


app.include_router(_quality_http_router(("check_creation_task_quality",)))




def _bind_quality_runtime():
    if device_runtime is None and not _start_device_runtime(start=False):
        raise RuntimeUnavailable("当前设备策略尚未装配，无法接续质检")
    return device_runtime


def _quality_application():
    return QualityApplication(production_repo,
        offline_reader=lambda: _quality_inspection_use_case(budget=None),
        current_runtime=lambda: device_runtime, bind_runtime=_bind_quality_runtime,
        lifecycle_lock=_runtime_reload_lock)


def _read_creation_quality_continuation(request, task_id, job_id, failed_request_id):
    workspace_id = current_workspace_id(request)
    _quality_check_task_access(request, workspace_id, task_id)
    actor = _require_permit_actor(request, workspace_id)
    return _quality_application().read_continuation(workspace_id, task_id, job_id, failed_request_id, actor)




app.include_router(_quality_http_router(("continue_phase", "original_continuation"),
    read_continuation=_read_creation_quality_continuation))




def _task_output_http_router(route_names):
    return build_task_output_router(
        readable_for=lambda: _readable_creation_task,
        output_case_for=lambda: _output_observation_use_case,
        workspace_for=lambda: current_workspace_id,
        output_error_for=lambda: OutputObservationRejected,
        http_error_for=lambda: HTTPException,
        file_response_for=lambda: FileResponse,
        tracks_for=lambda: _current_subtitle_tracks,
        track_converter_for=lambda: _execution_subtitle_track,
        replace_for=lambda: replace,
        bilingual_for=lambda: render_bilingual_srt,
        srt_for=lambda: render_srt,
        response_for=lambda: Response,
        production_for=lambda: production_repo,
        store_for=lambda: artifact_store,
        route_names=route_names,
    )


app.include_router(_task_output_http_router(("get_creation_task_output",)))


app.include_router(_task_output_http_router(("get_creation_task_subtitles",)))




def _timeline_version_or_404(
    workspace_id: str, task_id: str, version_id: str
) -> TimelineVersion:
    """Read one historical version only when it belongs to the requested task.

    ``get_timeline_version`` is workspace-scoped, so this task check is what
    keeps another task's version from being read through this route.
    """
    try:
        version = production_repo.timeline_records.get_timeline_version(workspace_id, version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="时间线版本不存在。") from exc
    if version.task_id != task_id:
        raise HTTPException(status_code=404, detail="时间线版本不存在。")
    return version












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








def _timeline_history_http_router(route_names):
    return build_timeline_history_router(
        production_for=lambda: production_repo,
        summary_type_for=lambda: TimelineVersionSummaryResponse,
        history_error_for=lambda: TimelineHistoryConflict,
        http_error_for=lambda: HTTPException,
        cover_for=lambda: _cover_asset_for_version,
        store_for=lambda: artifact_store,
        quote_for=lambda: quote,
        artifact_type_for=lambda: TimelineVersionArtifactResponse,
        not_generated_for=lambda: _HISTORY_ARTIFACT_NOT_GENERATED,
        missing_for=lambda: _HISTORY_ARTIFACT_MISSING,
        workspace_for=lambda: current_workspace_id,
        task_for=lambda: _creation_task_or_404,
        list_type_for=lambda: TimelineVersionListResponse,
        version_for=lambda: _timeline_version_or_404,
        shot_type_for=lambda: TimelineVersionShotResponse,
        segment_type_for=lambda: TimelineVersionNarrationSegmentResponse,
        detail_type_for=lambda: TimelineVersionDetailResponse,
        subtitle_type_for=lambda: SubtitleTrackResponse,
        asdict_for=lambda: asdict,
        cover_type_for=lambda: CoverAssetResponse,
        readable_for=lambda: _readable_creation_task,
        media_types_for=lambda: _HISTORY_ARTIFACT_MEDIA_TYPES,
        file_response_for=lambda: FileResponse,
        route_names=route_names,
    )


app.include_router(_timeline_history_http_router(("list_creation_task_timeline_versions",)))


app.include_router(_timeline_history_http_router(("get_creation_task_timeline_version",)))


app.include_router(_timeline_history_http_router(("get_creation_task_timeline_version_artifact",)))


app.include_router(_task_output_http_router(("get_creation_task_selected_subtitles",)))


app.include_router(_task_output_http_router(("get_creation_task_cover_image",)))




app.include_router(build_home_router(
    http_error_for=lambda: HTTPException,
    workspace_for=lambda: current_workspace_id,
    reader_for=lambda: _content_identity_http_adapter().reader,
    aggregate_for=lambda: aggregate_home,
    production_for=lambda: production_repo,
    sources_for=lambda: source_repository,
    private_for=lambda: _content_identity_http_adapter().include_private,
))


def _material_library_http_adapter():
    return MaterialLibraryHTTPAdapter(
        records_for=lambda: production_repo,
        search_for=lambda: production_repo.material_search,
        workspace_for=current_workspace_id,
        reader_for=_content_identity_http_adapter().reader,
        visible=_content_identity_http_adapter().visible,
        write_guard=_local_member_write_guard,
        library_root=lambda: settings.video_inbox_path,
        asset_response=_material_asset_response,
        knowledge_claims_for=lambda workspace_id, item_id, user_id, diagnostic: production_repo.list_knowledge_claims(
            workspace_id, item_id,
            knowledge_version=source_repository.get_knowledge_item_readable_by(
                workspace_id, item_id, user_id=user_id, local_diagnostic=diagnostic,
            ).version,
        ),
    )




app.include_router(build_material_insights_router(
    workspace_for=lambda: current_workspace_id,
    montages_for=lambda: montage_repo,
    reader_for=lambda: _content_identity_http_adapter().reader,
    response_for=lambda: MaterialLibraryInsightsResponse,
    production_for=lambda: production_repo,
    render_for=lambda: _render_stage_use_case,
))


app.include_router(build_material_library_router(_material_library_http_adapter, ('list_production_materials',)))


app.include_router(build_reference_video_router(
    workspace_for=lambda: current_workspace_id,
    reader_for=lambda: _content_identity_http_adapter().reader,
    visible_for=lambda: _content_identity_http_adapter().visible,
    production_for=lambda: production_repo,
))




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


def _wake_received_materials(repository):
    if material_ingestion is not None and material_ingestion.repository is repository:
        material_ingestion.wake()


def _material_intake_http_adapter():
    return MaterialIntakeHTTPAdapter(
        records_for=lambda: production_repo,
        discovery_for=lambda repository=None: (production_repo if repository is None else repository).material_discovery,
        workspace_for=current_workspace_id,
        reader_for=_content_identity_http_adapter().reader,
        member_scope=_member_owned_content_scope,
        ingestion_for=_material_ingestion_for,
        execution_mode=lambda: material_execution_mode,
        asset_response=_material_asset_response,
        discovery_exclusions=lambda discovery, workspace_id, owner: discovery.store.exclusions(workspace_id, owner),
        wake_received=_wake_received_materials,
    )


app.include_router(build_material_intake_router(_material_intake_http_adapter, ('get_material_intake_status',)))


app.include_router(build_material_intake_router(_material_intake_http_adapter, ('bind_material_intake',)))


app.include_router(build_material_intake_router(_material_intake_http_adapter, ('confirm_material_copy_complete',)))


app.include_router(build_material_intake_router(_material_intake_http_adapter, ('scan_material_intake',)))




# 只有真正的边界拒绝才算客户端可修的问题；其余照实报告来源返回的状态。




def _material_link_http_adapter():
    return MaterialLinkHTTPAdapter(
        records_for=lambda: production_repo,
        workspace_for=current_workspace_id,
        reader_for=_content_identity_http_adapter().reader,
        provenance_for=lambda repository, workspace_id, verified_owner_user_id, local_diagnostic: MaterialLinkProvenanceSink(
            repository.material_links, workspace_id, verified_owner_user_id, local_diagnostic),
    )


app.include_router(build_material_link_router(_material_link_http_adapter, ('check_production_material_link',)))


app.include_router(build_material_link_router(_material_link_http_adapter, ('import_production_material_link',)))


app.include_router(build_material_intake_router(_material_intake_http_adapter, ('retry_material_intake',)))


app.include_router(build_material_library_router(_material_library_http_adapter, ('update_material_asset_tags',)))


app.include_router(build_material_library_router(_material_library_http_adapter, ('update_material_asset_tags_batch',)))


app.include_router(build_material_library_router(_material_library_http_adapter, ('update_material_asset_notes',)))




app.include_router(build_material_catalog_router(_material_catalog_http_adapter, ('list_material_business_objects',)))


app.include_router(build_material_catalog_router(_material_catalog_http_adapter, ('create_material_business_object',)))


app.include_router(build_material_catalog_router(_material_catalog_http_adapter, ('list_material_asset_business_objects',)))


app.include_router(build_material_catalog_router(_material_catalog_http_adapter, ('save_material_asset_business_objects',)))


app.include_router(build_material_library_router(_material_library_http_adapter, ('list_material_segment_usage_records',)))


app.include_router(build_material_library_router(_material_library_http_adapter, ('get_unmatched_material_corrections',)))


app.include_router(build_material_library_router(_material_library_http_adapter, ('search_production_material_segments', 'read_material_candidate_bundle')))


app.include_router(build_material_library_router(_material_library_http_adapter, ('set_production_material_visibility',)))




app.include_router(build_material_intake_router(_material_intake_http_adapter, ('material_discovery_roots',)))


app.include_router(build_material_intake_router(_material_intake_http_adapter, ('latest_material_discovery',)))


app.include_router(build_material_intake_router(_material_intake_http_adapter, ('start_material_discovery',)))


app.include_router(build_material_intake_router(_material_intake_http_adapter, ('get_material_discovery',)))


app.include_router(build_material_intake_router(_material_intake_http_adapter, ('advance_material_discovery',)))


app.include_router(build_material_intake_router(_material_intake_http_adapter, ('control_material_discovery',)))


app.include_router(build_material_intake_router(_material_intake_http_adapter, ('receive_discovered_materials',)))


app.include_router(build_material_intake_router(_material_intake_http_adapter, ('preview_discovered_material',)))


app.include_router(build_material_library_router(_material_library_http_adapter, ('get_production_material',)))
app.include_router(build_material_evidence_router(records_for=lambda: production_repo,
    workspace_for=current_workspace_id, reader_for=_content_identity_http_adapter().reader))


app.include_router(build_material_library_router(_material_library_http_adapter, ('stream_production_material',)))




app.include_router(build_material_processing_router(
    workspace_for=lambda: current_workspace_id,
    reader_for=lambda: _content_identity_http_adapter().reader,
    mode_for=lambda: material_execution_mode,
    ingestion_for=lambda: _material_ingestion_for,
    production_for=lambda: production_repo,
    library_response_for=lambda: MaterialLibraryResponse,
    asset_response_for=lambda: _material_asset_response,
    http_error_for=lambda: HTTPException,
    visible_for=lambda: _content_identity_http_adapter().visible,
))


app.include_router(build_material_library_router(_material_library_http_adapter, ('update_material_use_state',)))


app.include_router(build_material_library_router(_material_library_http_adapter, ('review_production_material',)))


app.include_router(build_material_library_router(_material_library_http_adapter, ('update_production_material_segment',)))




def _legacy_montage_http_router(route_names):
    return build_legacy_montage_router(
        production_for=lambda: production_repo,
        workspace_for=lambda: current_workspace_id,
        batches_type_for=lambda: ProductionBatchesResponse,
        batch_response_for=lambda: _legacy_montage_response_adapter().production_batch_response,
        http_error_for=lambda: HTTPException,
        montage_for=lambda: montage_repo,
        plans_type_for=lambda: MontagePlansResponse,
        plan_response_for=lambda: _legacy_montage_response_adapter().montage_plan_response,
        safe_path_for=lambda: _safe_montage_media_path,
        file_response_for=lambda: FileResponse,
        candidate_type_for=lambda: MontageClipCandidateResponse,
        reader_for=lambda: _content_identity_http_adapter().reader,
        eligible_for=lambda: material_segment_eligible,
        route_names=route_names,
    )


app.include_router(_legacy_montage_http_router(("list_production_batches",)))




app.include_router(_legacy_montage_http_router(("get_production_batch",)))






















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






app.include_router(_legacy_montage_http_router(("list_production_montage_plans",)))






app.include_router(_legacy_montage_http_router(("get_production_montage_plan",)))




app.include_router(_legacy_montage_http_router(("stream_production_montage_output",)))


app.include_router(_legacy_montage_http_router(("stream_production_montage_subtitle",)))


def _refresh_montage_review_state(plan) -> None:
    return metadata_refresh_montage_review_state(plan)


app.include_router(_legacy_montage_http_router(("list_production_montage_clip_candidates",)))








app.include_router(_application_info_http_router(("get_version",)))


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


def _runtime_settings_http_router(route_names):
    return build_runtime_settings_router(
        workspace_for=lambda: current_workspace_id,
        settings_for=lambda: settings,
        auth_for=lambda: auth_context,
        workspace_settings_for=lambda: _workspace_settings_for,
        health_for=lambda: runtime_health,
        setup_for=lambda: setup_preflight,
        storage_preflight_for=lambda: workspace_preflight,
        local_host_for=lambda: is_local_client_host,
        device_values_for=lambda: device_runtime_settings_values,
        env_updates_for=lambda: runtime_settings_updates,
        settings_type_for=lambda: Settings,
        catalog_builder_for=lambda: model_catalog_from_settings,
        installer_for=lambda: model_installer,
        reload_lock_for=lambda: _runtime_reload_lock,
        reload_allowed_for=lambda: _assert_runtime_reload_allowed,
        prepare_reload_for=lambda: _prepare_runtime_reload,
        quiesce_for=lambda: _quiesced_runtime_drivers,
        save_for=lambda: apply_runtime_settings_save,
        activate_for=lambda: _activate_runtime_reload,
        env_path_for=lambda: runtime_env_path,
        route_names=route_names,
    )


app.include_router(_runtime_settings_http_router(("get_runtime_health",)))


app.include_router(_runtime_settings_http_router(("get_setup_preflight",)))


app.include_router(_runtime_settings_http_router(("complete_onboarding",)))


app.include_router(_runtime_settings_http_router(("get_runtime_settings",)))


app.include_router(_runtime_settings_http_router(("update_runtime_settings",)))



app.include_router(_runtime_settings_http_router(("is_setup",)))


def _assert_runtime_reload_allowed() -> None:
    if device_runtime is not None and "device_preparation" in device_runtime.registrations:
        raise HTTPException(409, "请先收束当前设备准备请求，再重载运行配置。")
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
    _stop_material_ingestion()
    if database_changed:
        old_platform_auth.sessions.close_all()

    model_installer.rebind_catalog(prepared.dependencies.models.catalog)
    publish_process_temporary_directory(prepared.temporary_root)
    _publish_runtime_dependencies(prepared.dependencies)
    _publish_runtime_routes(prepared.routes)
    creation_execution = prepared.creation
    automation_runtime = None
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




app.include_router(build_knowledge_notes_router(
    vault_for=lambda: vault,
    response_for=lambda: KnowledgeNoteResponse,
))


def _source_connector_response(connector: SourceConnector) -> SourceConnectorResponse:
    return SourceConnectorResponse.model_validate(connector, from_attributes=True)


def _source_item_http_router(route_names):
    return build_source_item_router(
        records_for=lambda: source_repository,
        workspace_for=lambda request: current_workspace_id(request),
        reader_for=lambda request, workspace_id: _content_identity_http_adapter().reader(request, workspace_id),
        sync_for=lambda workspace_id, connector: _sync_connector(workspace_id, connector),
        failure_for=lambda failure: _source_failure_http_exception(failure),
        html_client_for=lambda urls: HtmlPageSourceClient(urls),
        parse_for=lambda filename, data: parse_knowledge_file(filename, data),
        hermes_path_for=lambda: settings.hermes_radar_path,
        topic_urls_for=lambda: settings.topic_source_urls,
        urls_required_for=lambda: _EXTERNAL_URLS_CONFIGURATION_REQUIRED,
        route_names=route_names,
    )


def _knowledge_http_router(route_names):
    return build_knowledge_router(
        records_for=lambda: source_repository,
        workspace_for=lambda request: current_workspace_id(request),
        reader_for=lambda request, workspace_id: _content_identity_http_adapter().reader(request, workspace_id),
        write_guard=lambda request: _local_member_write_guard(request),
        governance_for=lambda: production_repo.material_data_governance,
        route_names=route_names,
    )


def _source_run_response(run) -> SourceRunEvidenceResponse:
    return SourceRunEvidenceResponse.model_validate(run, from_attributes=True)






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




def _source_connector_http_router(route_names):
    return build_source_connector_router(
        records_for=lambda: source_repository,
        workspace_for=lambda request: current_workspace_id(request),
        connector_response_for=lambda connector: _source_connector_response(connector),
        run_response_for=lambda run: _source_run_response(run),
        sync_for=lambda workspace_id, connector: _sync_connector(workspace_id, connector),
        route_names=route_names,
    )


app.include_router(_source_connector_http_router(('list_source_connectors', 'create_source_connector', 'get_source_connector', 'list_source_connector_runs', 'get_latest_source_connector_run', 'get_source_connector_run', 'sync_source_connector_endpoint')))














app.include_router(_source_item_http_router(("list_persisted_source_items",)))


app.include_router(_source_item_http_router(("create_manual_source_item",)))


app.include_router(_knowledge_http_router(("save_source_item_to_knowledge",)))


app.include_router(_source_item_http_router(("import_knowledge_file",)))


app.include_router(_knowledge_http_router(("download_knowledge_original",)))


app.include_router(_knowledge_http_router(("search_knowledge_items",)))


app.include_router(_knowledge_http_router(("get_knowledge_item_detail",)))


app.include_router(_knowledge_http_router(("list_knowledge_claims",)))


app.include_router(_knowledge_http_router(("create_knowledge_claim",)))


app.include_router(_knowledge_http_router(("update_knowledge_claim_status",)))


app.include_router(_knowledge_http_router(("edit_knowledge_item_content",)))


app.include_router(_knowledge_http_router(("set_knowledge_item_visibility_route",)))


app.include_router(_knowledge_http_router(("list_knowledge_items",)))


app.include_router(_source_item_http_router(("get_source_items",)))


app.include_router(_source_item_http_router(("sync_source_items",)))


app.include_router(_source_item_http_router(("ingest_manual_link",)))


app.include_router(_source_item_http_router(("capture_web_snapshot",)))


app.include_router(_source_connector_http_router(("get_source_sync_status",)))


def _preference_http_router(route_names):
    return build_preference_router(
        episodes_for=lambda: repo,
        workspace_for=lambda request: current_workspace_id(request),
        route_names=route_names,
    )


app.include_router(_preference_http_router((
    "get_feedback_summary", "get_preferences", "disable_preference", "restore_preference",
)))


app.include_router(build_video_intake_router(
    inbox_for=lambda: video_inbox,
    pipeline_for=lambda: video_pipeline,
    drafts_for=lambda: draft_workspace,
    feature_for=lambda: _require_feature,
    path_for=lambda: safe_processed_video_path,
    settings_for=lambda: settings,
    file_response_for=lambda: FileResponse,
    pending_type_for=lambda: PendingVideoResponse,
    processed_type_for=lambda: ProcessedVideoResponse,
    status_type_for=lambda: DraftStatusResponse,
    environment_type_for=lambda: DraftEnvironmentResponse,
    asset_type_for=lambda: DraftAssetResponse,
))




















app.include_router(_episode_read_http_router(("get_episode",)))




app.include_router(_episode_read_http_router(("get_episode_retake_report",)))


app.include_router(_episode_read_http_router(("get_episode_edit_plan",)))






app.include_router(_episode_read_http_router(("get_latest_retake_report",)))


























app.include_router(build_voice_transcription_router(
    http_error_type_for=lambda: HTTPException,
    transcribe_for=lambda: transcribe_audio_bytes,
    settings_for=lambda: settings,
    response_type_for=lambda: VoiceTranscriptionResponse,
))


# ── Creator Profile ──────────────────────────────────────────────


def _creator_profile_response(profile) -> CreatorProfileResponse:
    return CreatorProfileResponse(
        id=profile.id, revision=profile.revision,
        common_expressions=profile.common_expressions,
        banned_words=profile.banned_words,
        style_preferences=profile.style_preferences,
        created_at=profile.created_at, updated_at=profile.updated_at,
    )


def _creator_profile_http_router():
    return build_creator_profile_router(
        workspace_for=lambda request: current_workspace_id(request),
        reader_for=lambda request, workspace_id: _content_identity_http_adapter().reader(request, workspace_id),
        member_for=lambda request, workspace_id: _content_identity_http_adapter().assistant_member(request, workspace_id),
        database_for=lambda: settings.database_path,
        conversations_for=lambda: conversation_repo,
        profile_response=_creator_profile_response,
    )


app.include_router(_creator_profile_http_router())

app.include_router(_episode_read_http_router(("get_episode_production_chain",)))


app.include_router(_episode_read_http_router(("get_episode_source_file_states",)))




app.include_router(_episode_read_http_router(("list_episodes",)))


app.include_router(_episode_read_http_router(("list_episode_history",)))


app.include_router(_source_item_http_router(("retry_source_sync",)))


app.include_router(_preference_http_router((
    "get_preferences_cards", "get_preference_card_evidence",
)))


app.include_router(_episode_read_http_router(("get_episode_topic_evidence",)))


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
    return CreationExecutionControl(production_repo, creation_execution, _render_stage_use_case(), _verify_creation_identity,
        segments=_segment_production_use_case())


def _new_creation_execution(repository: ProductionRepository) -> CreationExecution:
    return CreationExecution(
        repository,
        run_montage_plan=_create_creation_task_montage_plan_for_workspace,
        run_render=_render_creation_task_montage_for_workspace,
        render_is_valid=_creation_render_is_valid,
    )


def _recover_creation_executions() -> None:
    _creation_execution_control().recover(device_runtime)
    if device_runtime is not None:
        device_runtime.registrations['creation_job'].owner.segments.recover(device_runtime)


def _creation_runtime():
    with _runtime_reload_lock:
        return _creation_runtime_locked()



def _creation_runtime_locked():
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
            return report_creation_identity_unknown((actor.device_id, actor.user_id, actor.workspace_id),
                source="creation_identity", reason="transport_unavailable")
        try:
            service = DeviceAccountService(settings, trusted_device_credential(), creation_permit_client=client)
        except (DeviceAccountError, HTTPException) as exc:
            return report_creation_identity_unknown((actor.device_id, actor.user_id, actor.workspace_id),
                source="creation_identity", reason="device_credential_unavailable", exception_type=type(exc).__name__)
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




def _creation_execution_summary(workspace_id: str, task_id: str, *, actor=None) -> dict[str, object]:
    return CreationExecutionControl(production_repo, creation_execution, None, _verify_creation_identity).status_summary(
        workspace_id, task_id, actor=actor)


def _creation_execution_status(workspace_id: str, task_id: str, *, actor=None) -> dict[str, object]:
    try:
        return _creation_execution_control().status(workspace_id, task_id, verify_media=device_runtime is None,actor=actor)
    except ExecutionControlRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


app.include_router(build_creation_execution_router(
    control_for=lambda: _creation_execution_control(),
    workspace_for=lambda request: current_workspace_id(request),
    readable_task=lambda request, task_id: _readable_creation_task(request, task_id),
    task_for=lambda workspace_id, task_id: _creation_task_or_404(workspace_id, task_id),
    request_actor=lambda request, workspace_id: _request_permit_actor(request, workspace_id),
    require_actor=lambda request, workspace_id: _require_permit_actor(request, workspace_id),
    require_task_open=lambda request, workspace_id, task_id: _require_automation_task_open(request, workspace_id, task_id),
    runtime_for_execution=lambda: _creation_runtime(),
    current_runtime=lambda: device_runtime,
    list_job_runs=lambda workspace_id, task_id: production_repo.job_records.list_job_runs(workspace_id, task_id),
    assistant_source=lambda task_id, request, turn_id, action, digest: _assistant_execution_source(
        task_id, request, turn_id, action, digest),
    bind_assistant_result=lambda task_id, request, turn_id, result, *, source: _bind_assistant_execution_result(
        task_id, request, turn_id, result, source=source),
    read_bound_result=lambda task_id, request, job_id: _read_bound_execution_result(task_id, request, job_id),
))









app.include_router(build_device_session_router(
    workspace_for=lambda: current_workspace_id,
    require_actor_for=lambda: _require_permit_actor,
    request_actor_for=lambda: _request_permit_actor,
    runtime_for=lambda: _creation_runtime,
    control_for=lambda: _creation_execution_control,
    repository_for=lambda: production_repo,
    execution_for=lambda: creation_execution,
    heartbeat_type_for=lambda: SessionHeartbeat,
    executor_type_for=lambda: CreationJobUnitExecutor,
    quality_kind_for=lambda: QUALITY_KIND,
    cover_kind_for=lambda: COVER_JOB_KIND,
    person_kind_for=lambda: PERSON_EDIT_KIND,
    narration_kind_for=lambda: NARRATION_EDIT_KIND,
    task_media_kind_for=lambda: TASK_MEDIA_KIND,
))


creation_execution: CreationExecution
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




def _device_preparation_service(kind="cpu"):
    from app.device_resource_configuration import current_device_identity
    from app.device_resource_preparation import DeviceResourcePreparation
    if settings.device_resource_mode != "normal" or not settings.device_resource_profile_path:
        raise DeviceProfileUnavailable("请使用已绑定设备路径的正常资源模式准备能力。")
    arguments = dict(
        profile_path=settings.device_resource_profile_path,
        device=current_device_identity(os.environ), settings=settings,
        write_paths={"database": Path(production_repo.database_path).resolve().parent,
                     "output": Path(settings.video_output_path).expanduser(),
                     "temporary": settings.effective_temporary_root_path},
        verify_identity=_verify_creation_identity)
    if kind == "cpu":
        return DeviceResourcePreparation(**arguments)
    if kind == "narration":
        from app.narration_resource_preparation import NarrationResourcePreparation
        return NarrationResourcePreparation(repository=production_repo, model_catalog=model_catalog,
                                             provider=voice_clone_provider, **arguments)
    if kind == "montage":
        from app.montage_resource_preparation import MontageResourcePreparation
        return MontageResourcePreparation(repository=production_repo,
            montage_stage=_montage_stage_use_case(), task_authorizations=_task_authorizations_for_goal(), **arguments)
    raise ValueError("未知设备准备类型")


def _publish_device_runtime(runtime):
    global device_runtime
    device_runtime = runtime


def _device_preparation_application(*, kind="cpu", request_id=None, actor=None):
    from app.device_preparation_application import DevicePreparationApplication
    from app.device_resource_preparation import OWNER_TYPE, CpuPreparationShape, request_kind
    from app.narration_resource_preparation import NarrationPreparationShape
    from app.montage_resource_preparation import MontagePreparationShape
    if request_id is not None:
        kind = request_kind(database_path=production_repo.database_path, request_id=request_id, actor=actor)
    def bind(service, budget, scope):
        runtime, _ = bind_device_runtime(
            repository=production_repo, settings=settings, members=local_members_service,
            registrations={OWNER_TYPE: service.registration()}, budget=budget,
            material_executor=None, verify_identity=_verify_creation_identity,
            record_admission=None, dispatch_owner=scope)
        return runtime
    return DevicePreparationApplication(
        service=_device_preparation_service(kind), database_path=production_repo.database_path,
        lifecycle_lock=_runtime_reload_lock, current_runtime=lambda: device_runtime,
        replace_runtime=_publish_device_runtime, stop_runtime=_stop_device_runtime, bind_runtime=bind,
        shape_factory={'cpu': CpuPreparationShape, 'narration': NarrationPreparationShape,
                       'montage': MontagePreparationShape}[kind])


def _dispatch_device_preparation(action, *, actor, request_id, shape=None, kind="cpu"):
    application = (_device_preparation_application(kind=kind) if action == "prepare"
                   else _device_preparation_application(request_id=request_id, actor=actor))
    return application.dispatch(action, actor=actor, request_id=request_id, shape=shape)



app.include_router(build_device_preparation_router(
    authorized_actor=lambda request: _require_permit_actor(request, current_workspace_id(request)),
    application=_device_preparation_application,
    profile_status=lambda actor: resource_preparation_status(settings, os.environ, model_catalog, actor),
    actor_scope=lambda actor: preparation_actor_scope(actor, os.environ)))


def _build_device_components(*, repository: ProductionRepository):
    """装出这台机器唯一的整机运行时：一把锁、一张队列、一个驱动线程。

    ``creation_job`` 始终在这里注册——交互式许可已经获批，不能因为定时素材
    通道关闭就一起消失。``material_asset`` 只在它自己的定时模式确实是 scheduled
    时才注册，避免造出第二条永远等不到许可的队列。探针和预算都是真实来源：
    没标定就抛 ``HostBudgetUncalibrated``，由调用方明确报不可用。
    """
    from app.device_runtime import OwnerRegistration
    from app.material_ingestion import (
        MaterialHashOwnerVerifier, MaterialHashUnitExecutor,
    )
    profile = _device_resource_profile()
    budget = profile.budget
    cpu_render_budget, audio_budget = budget.cpu_render, budget.cpu_audio
    narration_budget, montage_budget = budget.narration, budget.montage
    presenter_budget, presenter_cpu_render_budget = budget.presenter, budget.presenter_cpu_render
    from app.creation.packet_copy_assembly import PacketCopyAssembly
    packet_copy = (PacketCopyAssembly(repository, artifact_store, settings.effective_temporary_root_path,
                                     budget=budget.packet_copy, reserve_bytes=budget.min_free_disk_bytes)
                   if budget.packet_copy is not None else None)
    from app.creation.audio_delivery_assembly import AudioDeliveryAssembly
    from app.creation.picture_fragment_assembly import PictureFragmentAssembly
    task_audio, metadata, picture_renderer = _picture_preview_components(repository)
    picture_fragments = (PictureFragmentAssembly(repository, artifact_store, settings.effective_temporary_root_path,
        metadata=metadata, picture_renderer=picture_renderer, render_budget=cpu_render_budget,
        packet_copy_assembly=packet_copy)
        if packet_copy is not None and cpu_render_budget is not None else None)
    audio_delivery = (AudioDeliveryAssembly(repository, artifact_store, settings.effective_temporary_root_path,
        audio=task_audio, packet_copy_assembly=packet_copy, audio_budget=budget.audio_encode,
        mux_budget=budget.final_mux, reserve_bytes=budget.min_free_disk_bytes)
        if packet_copy is not None and budget.audio_encode is not None and budget.final_mux is not None else None)
    from app.creation.task_authorization import TaskAuthorizationUseCase
    task_authorizations = TaskAuthorizationUseCase(repository, lambda identity: _verify_creation_identity(
        CreationExecutionActor.from_event(identity["workspace_id"], identity)))
    quality = _quality_inspection_use_case(repository, task_authorizations=task_authorizations, budget=budget.quality)
    cover = _cover_use_case(repository, budget=budget.cover)
    task_media = TaskMediaJobs(repository, facts=_task_media_facts,
        known_failure_secrets_for=lambda workspace_id: (
            _workspace_settings_for(workspace_id).effective_llm_api_key,
            settings.tencentcloud_secret_id, settings.tencentcloud_secret_key),
        verify_identity=_verify_creation_identity, on_settled=_wake_assistant_continuations,
        audio_ingress=_audio_ingress_use_case(repository),
        audio_edit=_audio_edit_use_case(repository),
        output_observation=_output_observation_use_case(repository),
        media_script=_media_script_use_case(repository),
        media_script_for_task=lambda task_id: _media_script_use_case(repository,task_id=task_id),
        visual_preview=_visual_preview_use_case(repository),
        compound_selection=_compound_edit_use_case(repository),
        exact_preview=_exact_preview_use_case(repository),
        command_preview=_command_preview_use_case(repository),
        clip_preview=ClipPreviewExecutor(_clip_preview_use_case(repository),
            TaskAudio(repository, artifact_store, settings.effective_temporary_root_path)))
    cover.task_authorizations = task_authorizations
    task_media.task_authorizations = task_authorizations
    registrations = {
        QUALITY_KIND: quality_check_registration(quality),
        "creation_job": creation_job_registration(
            repository,
            task_media=task_media,
            audio_assembly=task_audio,
            packet_copy_assembly=packet_copy,
            audio_delivery=audio_delivery,
            picture_fragments=picture_fragments,
            source_verifier=lambda source,actor,connection=None:validate_edit_origin(conversation_repo,source,actor,connection=connection),
            cover=cover,
            task_authorizations=task_authorizations,
            person_stage=_person_stage_use_case(repository),
            montage_stage=_montage_stage_use_case(repository),
            narration_stage=_narration_stage_use_case(repository,narration_budget=narration_budget),
            narration_runtime_facts=lambda workspace_id,task_id: _narration_runtime_facts(
                workspace_id,task_id,repository=repository),
            graphic_source_facts=lambda workspace_id, task_id, reference, *, actor, connection=None: graphic_source_facts(
                repository, artifact_store, workspace_id, task_id, reference, actor=actor, connection=connection),
            audio_mix_facts=lambda workspace_id, task_id, montage, *, actor, connection=None: _task_audio().mix_facts(
                workspace_id, task_id, montage.audio_timeline, montage.duration_seconds, actor=actor,
                normalize_voice_sources=montage.narration_linkage_created_audio_timeline, budget=audio_budget, connection=connection),
            render_action=_render_creation_stage_with_grant,
            render_is_valid=_creation_stage_valid_with_grant,
            cpu_render_budget=cpu_render_budget,
            verify_identity=_verify_creation_identity,
            narration_budget=narration_budget, narration_audio_budget=audio_budget, montage_budget=montage_budget,
            presenter_budget=presenter_budget, presenter_cpu_render_budget=presenter_cpu_render_budget,
            presenter_models=_presenter_model_ids(), resolve_presenter_output=_person_render_media_path,
        ),
    }
    from app.automation_runtime import restrict_automation_registration
    creation_owner=registrations['creation_job'].owner
    segments = _segment_production_use_case(repository)
    segments.narration_stage = registrations['creation_job'].executor.narration_stage
    creation_owner.segments = segments
    registrations['creation_job'].executor.segments = segments
    native_case = _native_task_operations_case(repository,budget=budget.native_command,authorizations=task_authorizations)
    creation_owner.native_case = native_case
    registrations['creation_job'].executor.native_case = native_case
    registrations["creation_job"] = restrict_automation_registration(registrations["creation_job"])
    creation_registration = registrations["creation_job"]
    registrations["creation_job"] = replace(creation_registration,
        on_stopped=creation_registration.executor.record_stopped)
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
        verify_identity=_verify_creation_identity, record_admission=creation_owner.record_admission,
    )
    runtime.resource_profile = profile
    runtime.task_authorizations = task_authorizations
    registrations["creation_job"].executor.on_render_completed = lambda workspace_id, task_id, job_id: quality.start_for_render(
        runtime, workspace_id, task_id, job_id)
    return runtime, material_service


def _new_material_ingestion():
    from app.material_ingestion import MaterialIngestionService
    return MaterialIngestionService(production_repo, scan_interval_seconds=60, stable_seconds=10)


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
    if service is not None:
        if not service.stop(timeout_seconds=30):
            raise RuntimeError("素材接收线程尚未退出，原运行绑定保持。")
        material_ingestion = None


def _start_device_runtime(*, start: bool = True) -> bool:
    with _runtime_reload_lock:
        return _start_device_runtime_locked(start=start)



def _start_device_runtime_locked(*, start: bool = True) -> bool:
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
    with _runtime_reload_lock:
        _stop_device_runtime_locked()



def _stop_device_runtime_locked() -> None:
    global device_runtime
    runtime = device_runtime
    if runtime is not None:
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
            list_templates=lambda actor:[*VideoTemplateUseCase(production_repo).builtin_templates(actor.workspace_id),
                *production_repo.video_templates.list_video_templates(actor.workspace_id,
                    viewer_user_id=actor.user_id or actor.member_id,local_diagnostic=False)],
            preflight=_creation_plan_preflight_http_adapter().preflight,preview_response=_creation_task_response_adapter().creation_plan_preview_response,
            plan_response=_creation_task_response_adapter().creation_plan_response,execution_status=_creation_execution_status,
            sync_connector=_sync_connector,inspect_topic_route=_workspace_llm_route_snapshot().snapshot,
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
        if not automation_runtime.stop():
            raise RuntimeError("自动任务驱动尚未退出，原运行绑定保持。")
        automation_runtime = None


@app.on_event("startup")
def recover_interrupted_assistant_turns() -> None:
    conversation_repo.initialize()
    recovery = ScriptGenerationIntent(production_repo, conversation_repo,
        source_inputs=_source_inputs_use_case().prepare, route_snapshot={}, verify_identity=_verify_creation_identity)
    conversation_repo.fail_interrupted_turns(recover_user_action=recovery.recover_interrupted)


assistant_continuation_driver = None
_goal_continuation_service = None


def _task_authorizations_for_goal(repository=None):
    repo = production_repo if repository is None else repository
    if device_runtime is not None and repo is production_repo and device_runtime.task_authorizations is not None:
        return device_runtime.task_authorizations
    from app.creation.task_authorization import TaskAuthorizationUseCase
    return TaskAuthorizationUseCase(repo,lambda identity:_verify_creation_identity(
        CreationExecutionActor.from_event(identity['workspace_id'],identity)))


def _bind_goal_business_scope(request,goal):
    authorizations=_task_authorizations_for_goal()
    actor=_require_permit_actor(request,goal['workspace_id'])
    task=production_repo.task_records.get_creation_task(goal['workspace_id'],goal['task_id'])
    profile=get_creator_profile(settings.database_path,workspace_id=task.workspace_id,owner_user_id=goal['member_id'])
    memory=(_creator_profile_response(profile).model_dump(mode='json') if profile is not None else
        {'id':'','revision':0,'common_expressions':[],'banned_words':[],'style_preferences':{}})
    context=goal['request_context']
    references={'selected_references':context.get('target_snapshot',{}).get('selected_references',[])}
    if context.get('profile_reference'):
        ref=context['profile_reference']
        references['profile_reference']=_assistant_profile_snapshot(request,ref['kind'],ref['profile_id'],ref['version'])
    if context.get('knowledge_reference'):
        ref=context['knowledge_reference']
        references['knowledge_reference']=_assistant_knowledge_snapshot(request,ref['knowledge_item_id'],ref['version'],ref.get('locator'))
    def current(conn):
        root=conversation_repo._goal_root(conn,goal['original_turn_id'])
        if (root is None or not conversation_repo._goal_revision_matches(root,goal)
                or not conversation_repo._goal_origin_current(conn,root)):
            raise TaskBusinessRejected('原目标在签发期间已变化',kind='conflict')
    authorizations.bind_business_context(task,actor,goal,memory=memory,references=references,write_guard=current)


def _creator_memory_facts(workspace_id, member_id):
    profile = get_creator_profile(settings.database_path, workspace_id=workspace_id, owner_user_id=member_id)
    return (_creator_profile_response(profile).model_dump(mode="json") if profile is not None else
            {"id": "", "revision": 0, "common_expressions": [], "banned_words": [], "style_preferences": {}})


def _task_business(*, generation_case=None):
    return TaskBusiness(production_repo,conversation_repo,_task_authorizations_for_goal(),runtime=_creation_runtime,
        execution=None if generation_case is not None else _creation_execution_control(),workspace_facts=_task_workspace_response_adapter().response,
        script_context=lambda ws, task_id, reader_id: _script_context_use_case().read_context(
            ws, task_id, reader_id, local_diagnostic=False),
        script_facts=lambda ws, task: _creation_task_text_response_adapter().creation_task_scripts_response(ws, task, include_body=False),
        script_case=lambda actor:(_generation_script_case(actor.workspace_id, actor.user_id or actor.member_id, generation_case)
            if generation_case is not None else _script_draft_use_case(actor.workspace_id,validate_inputs=lambda ws,plan,refs:
            _validate_script_input_sources(ws,plan,refs,actor.user_id or actor.member_id),
            content_reader=lambda ws:(actor.user_id or actor.member_id,False))),
        generate_topics=lambda ws,task,guard:_script_draft_use_case(ws,validate_inputs=lambda *args:None).generate_topics(ws,task,write_guard=guard),
        topic_facts=_creation_task_text_response_adapter().creation_task_topic_response,edit_capabilities=_task_edit_capability_response_adapter().facts,
        script_capabilities=_task_script_capability_facts,tool_capabilities=_script_tool_capability_facts,
        material_facts=_material_asset_response,observation_facts=lambda task,actor:_task_observation_response_adapter().metadata(task,actor.user_id or actor.member_id),
        observe_output=_submit_goal_observation,request_result=_public_goal_request_metadata,media_result=_goal_media_metadata,
        record_adoption=lambda *args, **kwargs: _goal_service().record_script_adoption(*args, **kwargs),
        segments=None if generation_case is not None else _segment_production_use_case(),
        native_case=None if generation_case is not None else _native_task_operations_case(),
        timeline_restores=None if generation_case is not None else _timeline_restore_use_case(),exact_case_for=_exact_preview_use_case,
        edit_controls=None if generation_case is not None else _edit_control_use_case(),
        media_jobs_list=_list_goal_media_jobs,execution_capabilities=_task_execution_capability_facts,
        device_preparation=_dispatch_device_preparation, quality=_quality_application(),
        generation_case=generation_case, creator_memory=_creator_memory_facts,
        execution_status=_creation_execution_summary, media_binding=_media_result_binding(),
        read_script_sections=lambda task, script_id, **values:
            production_repo.script_records.read_creation_task_script_sections(task.workspace_id, task.id, script_id, **values))


def _submit_goal_observation(task,actor,values,origin,guard):
    payload=CreationTaskObservationRequest.model_validate(values)
    case=_output_observation_use_case()
    previous=case.original_request(task,actor,payload)
    if previous is not None:
        return previous
    try:
        version=case.version(task,payload)
    except ValueError as exc:
        raise OutputObservationRejected(409,str(exc)) from exc
    source={'workspace_id':task.workspace_id,'task_id':task.id,'member_id':actor.user_id or actor.member_id,
        'expected_timeline_version_id':task.current_timeline_version_id,'expected_timeline_sequence':version.sequence if version else 0,
        'actor_identity':actor.identity_payload(),'request_id':str(payload.request_id) if payload.request_id else None,
        'assistant_turn_id':str(payload.assistant_turn_id),'conversation_id':origin['conversation_id'],'raw_command':origin['raw_command'],
        **{key:origin[key] for key in ('input_id','input_turn_id') if key in origin}}
    runtime=_creation_runtime()
    return _output_observation_use_case().submit(runtime,runtime.registrations['creation_job'].owner.task_media,
        task,actor,payload,source,receipt_for=_media_result_receipt,write_guard=guard)


def _goal_request_metadata(task,actor,request_id,phase):
    event_id,event=_goal_projection().request_claim(task,actor.user_id or actor.member_id,str(request_id),phase)
    result=production_repo.execution_records.read_edit_request_result(task,event_id)
    return {'request_id':str(request_id),'phase':phase,'input_digest':event.payload['input_digest'],'task_id':task.id,
        'status':'recorded' if result else 'unknown','submitted_input':event.payload['inputs'],
        'result_status_code':result['status_code'] if result else None,'result':result['response'] if result else None}


def _public_goal_request_metadata(task,actor,request_id,phase):
    receipt = _goal_request_metadata(task,actor,request_id,phase)
    return {**receipt, "submitted_input": project_submitted_edit_input(phase, receipt["submitted_input"])}


def _goal_media_metadata(task,actor,job_id):
    media, requests = _media_result_read_components()
    job,event=media.read(task.workspace_id,job_id)
    _goal_projection().require_media_task(task.id,job)
    try:
        return _media_results().receipt(media,job,actor,requests)
    except MediaResultRejected as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


def _goal_script_source_slot(goal, job_id, actor):
    return _goal_projection().script_source_slot(goal, job_id, actor, artifact_store, _output_observation_use_case())








def _goal_readiness_timeline(workspace_id, version_id):
    return production_repo.timeline_records.get_timeline_version(workspace_id, version_id)


def _goal_readiness_script(workspace_id, script_id):
    return production_repo.script_records.get_creation_task_script(workspace_id, script_id)


def _goal_readiness_completed_sources(goal, actor):
    return _goal_projection().completed_sources(goal, actor, artifact_store, _output_observation_use_case())


def _goal_readiness_media(task, actor, job_id):
    try:
        return _goal_media_metadata(task, actor, job_id)
    except HTTPException as exc:
        raise GoalEvidenceUnavailable(exc.status_code, exc.detail) from exc.__cause__


def _goal_readiness_execution(business, goal, actor, *, job_id):
    try:
        return business.read_execution(goal, actor, job_id=job_id)
    except HTTPException as exc:
        raise GoalEvidenceUnavailable(exc.status_code, exc.detail) from exc.__cause__


def _goal_readiness_observations(task, member_id):
    try:
        return _task_observation_response_adapter().metadata(task, member_id)
    except HTTPException as exc:
        raise GoalEvidenceUnavailable(exc.status_code, exc.detail) from exc.__cause__


def _goal_readiness_registered_output(workspace_id, task_id, actor):
    try:
        return _registered_observation_output(workspace_id, task_id, CreationTaskObservationTarget(kind="final"), actor)
    except HTTPException as exc:
        raise GoalEvidenceUnavailable(exc.status_code, exc.detail) from exc.__cause__


def _goal_readiness_plan(task):
    try:
        return _creation_plan_for_task_or_404(task.workspace_id, task)
    except HTTPException as exc:
        raise GoalEvidenceUnavailable(exc.status_code, exc.detail) from exc.__cause__


def _goal_readiness_subtitles(task, version):
    try:
        return _current_subtitle_tracks(task.workspace_id, task, current_version=version)
    except HTTPException as exc:
        raise GoalEvidenceUnavailable(exc.status_code, exc.detail) from exc.__cause__


def _goal_readiness_resume(goal, task, actor, business, owner, *, read_request, read_media):
    return _goal_service().verify_resume_business(goal, task, actor, business, owner,
        read_request=read_request, read_media=read_media)


def _goal_readiness_use_case():
    # Keep each reader lazy: bootstrap/reload must never retain an old repository or transport.
    return GoalReadinessUseCase(projection_for=_goal_projection, business_for=_task_business,
        read_timeline=_goal_readiness_timeline, read_script=_goal_readiness_script,
        completed_sources=_goal_readiness_completed_sources, read_request=_goal_request_metadata,
        read_media=_goal_readiness_media, read_execution=_goal_readiness_execution,
        verify_resume=_goal_readiness_resume, observations=_goal_readiness_observations,
        registered_output=_goal_readiness_registered_output, read_plan=_goal_readiness_plan,
        read_subtitles=_goal_readiness_subtitles, safe_text=_assistant_stream_safe_text)






def _goal_projection():
    return GoalResultProjection(production_repo, conversation_repo)






def _goal_service():
    global _goal_continuation_service
    if _goal_continuation_service is None:
        _goal_continuation_service=GoalContinuationService(conversation_repo,verify_current_goal=_goal_readiness_use_case().verify,run_goal_turn=lambda claim, fresh: _task_turn_http_adapter().run_goal(claim, fresh))
    return _goal_continuation_service


def _register_adopted_creation_goals(request, turn_id, creation_request):
    if any(event.get('type')=='creation_request_resumed' for event in conversation_repo.get_turn(turn_id)['events']):
        _wake_assistant_continuations()
    workspace_id = current_workspace_id(request)
    member_id = _content_identity_http_adapter().reader(request, workspace_id)[0]
    with production_repo._connect() as conn:
        sources = conn.execute("SELECT id FROM task_events WHERE workspace_id=? "
            "AND event_type='source_task_created' AND json_extract(payload_json,'$.source.turn_id')=? "
            "AND json_extract(payload_json,'$.source.member_id')=?", (workspace_id, turn_id, member_id)).fetchall()
    source_goals = []
    for source in sources:
        event = production_repo.execution_records.get_task_event(workspace_id, source['id'])
        task = production_repo.task_records.get_creation_task(workspace_id, event.task_id)
        plan = production_repo.plan_records.get_creation_plan(workspace_id, task.plan_id)
        source_goals.extend(_goal_service().register_source_task(turn_id, workspace_id, member_id, event, task, plan))
    for root_id in source_goals:
        _bind_goal_business_scope(request, conversation_repo.get_task_goal(root_id))
    if source_goals:
        _wake_assistant_continuations()
    if creation_request is None:
        return
    # Never bind the model's returned task IDs. Read the owning repository's
    # actual window/tasks/adoption again through the same member boundary.
    current = _creation_request_response(_creation_request_or_404(request, creation_request.id)).model_dump(mode="json")
    registered = _goal_service().register_creation_request(turn_id, workspace_id, member_id, current)
    if registered:
        for root_id in registered:
            _bind_goal_business_scope(request,conversation_repo.get_task_goal(root_id))
        _wake_assistant_continuations()


def _assistant_execution_binding():
    return AssistantExecutionBinding(
        production_repo, conversation_repo, lambda: _creation_execution_control(), _goal_projection(),
        verify_identity=_verify_creation_identity,
        read_edit_receipt=lambda task, member_id, request_id, phase:
            _exact_preview_use_case().read_request_receipt(task, member_id, request_id, phase),
        encode_execution=jsonable_encoder)


def _execution_binding_http(exc):
    status = {'conflict':409, 'forbidden':403, 'unavailable':503,
              'invalid_receipt':502, 'not_found':404}[exc.kind]
    return HTTPException(status_code=status, detail=exc.detail)


def _read_bound_execution_result(task_id, request, job_id):
    task, _member_id = _exact_edit_access(task_id, request, write=True)
    actor = _request_permit_actor(request, task.workspace_id)
    try:
        return _assistant_execution_binding().read_bound(task, actor, job_id, verify_media=device_runtime is None)
    except ExecutionBindingRejected as exc:
        raise _execution_binding_http(exc) from exc
    except ExecutionControlRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc







def _bind_assistant_execution_observation(task_id, request, turn_id, details, result):
    workspace_id = current_workspace_id(request)
    member_id = _content_identity_http_adapter().assistant_member(request, workspace_id)
    binding = _assistant_execution_binding()
    try:
        prepared = binding.prepare_observation(workspace_id, task_id, member_id, turn_id, details)
        if prepared is None:
            return result
        actor = _request_permit_actor(request, workspace_id)
        source = binding.observation_source(prepared, actor, result)
        if source is None:
            return result
    except ExecutionBindingRejected as exc:
        raise _execution_binding_http(exc) from exc
    return _bind_assistant_execution_result(task_id, request, turn_id, result, source=source)


def _assistant_edited_execution_version(task, member_id, turn_id, origin, snapshot, version, request):
    try:
        return _assistant_execution_binding().edited_execution_version(
            task, member_id, turn_id, origin, snapshot, version,
            input_id=request.headers.get('X-Assistant-Input-ID') or None)
    except ExecutionBindingRejected as exc:
        raise _execution_binding_http(exc) from exc
    except (GoalProjectionRejected, EditPreviewRejected) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _assistant_execution_source(task_id, request, turn_id, action, input_digest=None):
    if turn_id is None:
        return None, None
    task, member_id = _exact_edit_access(task_id, request, write=True)
    binding = _assistant_execution_binding()
    try:
        prepared = binding.prepare_source(task, member_id, str(turn_id),
            input_id=request.headers.get('X-Assistant-Input-ID') or None,
            action=action, input_digest=input_digest)
        actor = _request_permit_actor(request, task.workspace_id)
        source, replay_job_id = binding.source(prepared, actor)
    except ExecutionBindingRejected as exc:
        raise _execution_binding_http(exc) from exc
    except (GoalProjectionRejected, EditPreviewRejected) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    replay = _read_bound_execution_result(task_id, request, replay_job_id) if replay_job_id is not None else None
    return source, replay


def _bind_assistant_execution_result(task_id, request, turn_id, candidate, *, source=None):
    binding = _assistant_execution_binding()
    try:
        job_id = binding.candidate_job_id(candidate)
        try:
            result = _read_bound_execution_result(task_id, request, job_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        workspace_id = current_workspace_id(request)
        member_id = _content_identity_http_adapter().assistant_member(request, workspace_id)
        result = binding.record_result(workspace_id, task_id, member_id, turn_id, result, source=source,
            bind_goal_scope=lambda goal: _bind_goal_business_scope(request, goal))
    except ExecutionBindingRejected as exc:
        raise _execution_binding_http(exc) from exc
    _wake_assistant_continuations()
    return result





def _read_authorized_assistant_execution(goal, job_id):
    business = _task_business()
    actor, _task, _scope = business.original(goal)
    return jsonable_encoder(business.read_execution(goal, actor, job_id=job_id))


def _assistant_execution_projector():
    from functools import partial
    return partial(_goal_projection().project_execution_results,
        read_authorized_execution=_read_authorized_assistant_execution,
        observe=lambda turn_id, event: _goal_service().observe_business_result(turn_id, event))


def _wake_assistant_continuations():
    if assistant_continuation_driver is not None:
        assistant_continuation_driver.wake.set()


def _mark_assistant_projection(candidate):
    _goal_projection().mark(candidate)


def _assistant_business_turn_can_continue(binding, turn_id):
    return _goal_projection().can_continue(binding, turn_id)






def _assistant_request_projection_receipt(task, event_id):
    recorded = _task_edit_receipt_http_adapter().read_edit_request_result(task, event_id)
    return {"status_code": recorded.status_code, "response": json.loads(recorded.body)} if recorded is not None else None


def _assistant_business_media_receipt(media, data, task_id, base_version_id):
    return _media_results().terminal_receipt(media, data, data['actor'], device_runtime.scheduler,
        task_id=task_id, base_version_id=base_version_id)


def _assistant_business_projector():
    from functools import partial
    return partial(_goal_projection().project_candidate,
        media_available=lambda: device_runtime is not None and "creation_job" in device_runtime.registrations,
        runtime_available=lambda: device_runtime is not None,
        read_goal_actor=lambda goal: _task_business().original(goal), verify_actor=_verify_creation_identity,
        read_request=_assistant_request_projection_receipt,
        media_for=lambda: device_runtime.registrations["creation_job"].owner.task_media,
        outputs_for=_output_observation_use_case,
        encode_preview=lambda preview: _edit_preview_response(*preview).model_dump(mode="json"),
        encode_media=_assistant_business_media_receipt,
        encode_request_job=lambda job, actor: _media_result_receipt(
            device_runtime.registrations["creation_job"].owner.task_media, job, actor, device_runtime),
        observe=lambda turn_id, event: _goal_service().observe_business_result(turn_id, event),
        wake=_wake_assistant_continuations)


def _assistant_script_tool_candidates():
    return _goal_projection().script_tool_candidates()


def _assistant_saved_script_tool_receipt(context):
    candidate = context.candidate
    located = candidate["located"]
    registry = _media_script_tools()
    tool = registry.get_created_for_request(context.actor, UUID(located["request_id"]))
    binding = AssistantScriptToolResultBinding(production_repo, conversation_repo, registry,
        verify_reader=lambda actor: _assistant_execution_binding().require_actor(actor))
    try:
        binding.check_save_result(candidate["workspace_id"], candidate["task_id"], tool, located["submitted_input"])
    except ScriptToolReceiptRejected as exc:
        raise HTTPException(502, exc.detail) from exc
    receipt = AssistantScriptToolReceipt(action="save", status="recorded", request_id=located["request_id"],
        tool_id=tool["tool_id"], tool_version=tool["version"], query_url=located["query_url"],
        submitted_input=located["submitted_input"], tool=tool_metadata(tool)).model_dump(mode="json")
    return receipt


def _assistant_script_tool_projector():
    from functools import partial
    return partial(_goal_projection().project_script_tool_candidate,
        verify_actor=_verify_creation_identity, read_saved_receipt=_assistant_saved_script_tool_receipt,
        observe=lambda turn_id, event: _goal_service().observe_business_result(turn_id, event),
        wake=_wake_assistant_continuations)


def _reconcile_assistant_business_results():
    projected = sum(_assistant_business_projector()(candidate)
                    for candidate in production_repo.execution_records.assistant_business_candidates())
    return projected + sum(_assistant_script_tool_projector()(candidate)
                           for candidate in _assistant_script_tool_candidates()) + _assistant_execution_projector()()


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
        _goal_service().start()
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
                        _goal_service().tick(limit=8)
            except Exception:
                logger.exception("对话业务结果回填失败；持久来源保留，未重复执行业务动作")

    def pause(self, timeout_seconds: float) -> bool:
        if timeout_seconds < 0:
            raise ValueError("暂停等待时间不能为负数")
        deadline=time.monotonic()+timeout_seconds
        if not self._tick_lock.acquire(timeout=max(0,deadline-time.monotonic())):
            return False
        try:
            was_paused=self._paused.is_set()
            self._paused.set()
            if not _goal_service().pause(max(0,deadline-time.monotonic())):
                if not was_paused:
                    self._paused.clear()
                return False
        finally:
            self._tick_lock.release()
        return True

    def resume(self) -> None:
        if self.stop_event.is_set():
            raise RuntimeError("对话结果驱动已终止，不能恢复原循环")
        _goal_service().resume()
        self._paused.clear()
        self.wake.set()

    def request_stop(self):
        _goal_service().request_stop()
        self.stop_event.set()
        self.wake.set()

    def stop(self, timeout_seconds=5, *, notify=True):
        self.request_stop()
        deadline = time.monotonic() + max(0, timeout_seconds)
        active_turns = stop_all_assistant_turns(deadline_monotonic=deadline) if notify else []
        if self.thread is not None:
            self.thread.join(timeout=max(0, deadline-time.monotonic()))
            if self.thread.is_alive():
                return False
        if not _goal_service().stop(deadline_monotonic=deadline):
            return False
        for turn_id in active_turns:
            conversation_repo.finish_turn(turn_id, "failed", result={"reason": "api_shutdown"})
        return True


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
        if not assistant_continuation_driver.stop():
            raise RuntimeError("对话结果驱动尚未退出，原运行绑定保持。")
        assistant_continuation_driver = None
    global _goal_continuation_service
    _goal_continuation_service=None


@app.on_event("shutdown")
def stop_active_assistant_turns() -> None:
    for turn_id in stop_all_assistant_turns():
        conversation_repo.finish_turn(turn_id, "failed", result={"reason": "api_shutdown"})


@app.on_event("startup")
def resume_persisted_creation_executions() -> None:
    with _runtime_reload_lock:
        if device_runtime is not None and device_runtime.running:
            return
        try:
            # Register the runtime first, recover all persisted owner jobs while its
            # dispatch loop is still stopped, then allow any queued unit to run.
            ready = _start_device_runtime(start=False)
            if device_runtime is not None and not ready:
                return  # Keep the stopped runtime and original work until release is provable.
            _start_material_ingestion()
            _recover_creation_executions()
            runtime = device_runtime
            if runtime is not None:
                runtime.registrations[QUALITY_KIND].owner.service.schedule_rendered_tasks(runtime)
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
            raise


@app.on_event("shutdown")
def stop_creation_execution_worker() -> None:
    _stop_device_runtime()
    _stop_material_ingestion()
    platform_auth_service.sessions.close_all()


app.on_event("startup")(start_assistant_continuation_driver)
# Shutdown hooks run in registration order; every transport user stops first.
app.on_event("shutdown")(stop_auth_transport)
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
    return app


# ── Server entry point (for standalone API server) ──────────────────
if __name__ == "__main__":
    import os

    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)
