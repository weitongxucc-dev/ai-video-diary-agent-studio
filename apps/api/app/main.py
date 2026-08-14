from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.auth import (
    ForbiddenError,
    OnboardingStateError,
    SupabaseAuthContext,
    UnauthorizedError,
)
from app.config import Settings, runtime_env_file_path
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
from app.preference_memory import PreferenceState, build_preference_memory
from app.preflight import setup_preflight
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
from app.schemas import (
    ApproveTopicRequest,
    ArchiveSourcesRequest,
    ArchiveSourcesResponse,
    ContentTemplateSelectRequest,
    ContentTemplatesResponse,
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
    FeedbackEventResponse,
    FeedbackRequest,
    FeedbackRevertRequest,
    FeedbackSummaryResponse,
    KnowledgeNoteResponse,
    ManualLinkRequest,
    MarkPublishedRequest,
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
    ProductionChainResponse,
    ProductionModeRequest,
    PublishMetricsUpdateRequest,
    RetakeReportResponse,
    RuntimeHealthResponse,
    RuntimeSettingsResponse,
    RuntimeSettingsUpdateRequest,
    ScriptRegenerateRequest,
    ScriptSectionRegenerateRequest,
    ScriptUpdateRequest,
    ScriptVersionRevertRequest,
    SetupPreflightResponse,
    SourceFileStatesResponse,
    SourceItemResponse,
    SourceItemsResponse,
    SourceSyncRetryResponse,
    SourceSyncStatusesResponse,
    SourceSyncStatusResponse,
    SystemStatusResponse,
    TopicEvidenceItemResponse,
    TopicEvidenceResponse,
    UploadVideoRequest,
    VoiceCommandRequest,
    VoiceCommandResponse,
    VoiceTranscriptionResponse,
    WorkflowStatusResponse,
)
from app.services import EpisodeService
from app.sources import ObsidianVault, TopicSourceItem
from app.topics import (
    TopicGenerationConfigurationError,
    TopicGenerationResponseError,
    topic_generator_from_settings,
)
from app.video import FfmpegVideoProcessor, SourceTracker, VideoInbox
from app.video_jobs import VideoJobRunner
from app.video_pipeline import video_processing_pipeline_from_settings
from app.voice import transcribe_audio_bytes
from app.voice_commands import VoiceCommandPlan, parse_voice_command
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
set_auth_enabled(settings.auth_enabled)
repo = EpisodeRepository(database_path=settings.database_path)
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
edit_plan_apply_runner = None
source_tracker = SourceTracker(video_inbox)
manual_source_items: list[TopicSourceItem] = []
auth_context = SupabaseAuthContext(settings)

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
}


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
            path not in ONBOARDING_REPAIR_PATHS
            and not auth_context.is_onboarding_complete(
                principal.user_id,
                principal.workspace_id or LEGACY_WORKSPACE_ID,
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
    import os as _os
    import shutil as _shutil

    llm_available = bool(settings.deepseek_api_key or _os.getenv("DEEPSEEK_API_KEY"))
    ffmpeg_available = _shutil.which("ffmpeg") is not None
    gates: dict[str, tuple[bool, str]] = {
        "generate-topics": (llm_available, "AI 模型未配置，请先在设置中配置 DeepSeek API Key"),
        "generate-script": (llm_available, "AI 模型未配置，请先在设置中配置 DeepSeek API Key"),
        "regenerate-section": (llm_available, "AI 模型未配置，请先在设置中配置 DeepSeek API Key"),
        "regenerate-script": (llm_available, "AI 模型未配置，请先在设置中配置 DeepSeek API Key"),
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
    settings = Settings.from_env()
    missing: list[str] = []
    if not settings.deepseek_api_key:
        missing.append("deepseek_api_key")
    configured = len(missing) == 0
    return {
        "configured": configured,
        "missing": missing,
        "next_step": "/welcome" if missing else "",
    }


def _reload_runtime_dependencies() -> None:
    global settings, repo, vault, video_inbox, video_processor, video_pipeline, draft_workspace, source_tracker, service, workflow, auth_context

    settings = Settings.from_env()
    set_auth_enabled(settings.auth_enabled)
    auth_context = SupabaseAuthContext(settings)
    repo = EpisodeRepository(database_path=settings.database_path)
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


@app.get("/api/sources/items", response_model=SourceItemsResponse)
def get_source_items() -> SourceItemsResponse:
    items = [
        *HermesRadarSourceClient(settings.hermes_radar_path).fetch_items(),
        *manual_source_items,
    ]
    return _source_items_response(items)


def _source_items_response(items: list[TopicSourceItem]) -> SourceItemsResponse:
    counts = Counter(item.source_type for item in items)
    return SourceItemsResponse(
        items=[SourceItemResponse.model_validate(item, from_attributes=True) for item in items],
        total_count=len(items),
        source_counts=dict(counts),
    )


@app.post("/api/sources/sync", response_model=SourceItemsResponse)
def sync_source_items() -> SourceItemsResponse:
    items = [
        *HermesRadarSourceClient(settings.hermes_radar_path).fetch_items(),
        *manual_source_items,
    ]
    if settings.topic_source_urls:
        try:
            items.extend(HtmlPageSourceClient(settings.topic_source_urls).fetch_items())
        except ExternalSourceResponseError:
            pass
    return _source_items_response(items)


@app.post("/api/sources/links", response_model=SourceItemsResponse)
def ingest_manual_link(request: ManualLinkRequest) -> SourceItemsResponse:
    url = request.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    try:
        items = HtmlPageSourceClient([url]).fetch_items()
    except ExternalSourceResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    manual_source_items.extend(items)
    return _source_items_response(manual_source_items)


@app.get(
    "/api/sources/sync-status",
    response_model=SourceSyncStatusesResponse,
)
def get_source_sync_status(request: Request) -> SourceSyncStatusesResponse:
    from app.repository import load_source_sync_statuses

    ws = current_workspace_id(request)
    raw_statuses = load_source_sync_statuses(workspace_id=ws)
    statuses = [
        {
            "source_type": source_type,
            "configured": data.get("configured", False),
            "last_sync_at": datetime.fromisoformat(data["last_sync_at"])
            if data.get("last_sync_at")
            else None,
            "last_success_at": datetime.fromisoformat(data["last_success_at"])
            if data.get("last_success_at")
            else None,
            "last_failure_at": datetime.fromisoformat(data["last_failure_at"])
            if data.get("last_failure_at")
            else None,
            "error_message": data.get("error_message"),
        }
        for source_type, data in raw_statuses.items()
    ]
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
def generate_script(episode_id: str) -> EpisodeResponse:
    episode = get_episode_or_404(episode_id)
    if episode.selected_topic_id is None:
        raise HTTPException(status_code=400, detail="approve a topic first")
    try:
        episode = workflow.run(
            "generate_script",
            episode,
            feedback_history=feedback_history_for(episode_id),
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


@app.post(
    "/api/episodes/{episode_id}/voice-command/preview",
    response_model=VoiceCommandResponse,
)
def preview_voice_command(
    episode_id: str,
    request: VoiceCommandRequest,
) -> VoiceCommandResponse:
    episode = recover_interrupted_video_job_if_needed(get_episode_or_404(episode_id))
    plan = parse_voice_command(episode, request.transcript)
    return _voice_command_response(plan)


@app.post(
    "/api/episodes/{episode_id}/voice-command/execute",
    response_model=VoiceCommandResponse,
)
def execute_voice_command(
    episode_id: str,
    request: VoiceCommandRequest,
) -> VoiceCommandResponse:
    episode = recover_interrupted_video_job_if_needed(get_episode_or_404(episode_id))
    plan = parse_voice_command(episode, request.transcript)
    if not plan.can_execute:
        return _voice_command_response(plan)

    try:
        updated_episode, edit_plan_workspace = _execute_voice_command_plan(
            episode,
            plan,
        )
    except (
        LLMConfigurationError,
        TopicGenerationConfigurationError,
    ) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (
        LLMResponseError,
        TopicGenerationResponseError,
    ) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _voice_command_response(
        plan,
        status="executed",
        result_episode=updated_episode,
        result_edit_plan=edit_plan_workspace,
    )


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


def _voice_command_response(
    plan: VoiceCommandPlan,
    *,
    status: str | None = None,
    result_episode: Episode | None = None,
    result_edit_plan: dict[str, object] | None = None,
) -> VoiceCommandResponse:
    return VoiceCommandResponse.model_validate(
        {
            "status": status or plan.status,
            "intent": plan.intent,
            "summary": plan.summary,
            "can_execute": plan.can_execute,
            "next_url": plan.next_url,
            "operations": [
                {
                    "kind": operation.kind,
                    "label": operation.label,
                    "params": operation.params,
                }
                for operation in plan.operations
            ],
            "result_episode": episode_response(result_episode)
            if result_episode is not None
            else None,
            "result_edit_plan": result_edit_plan,
        }
    )


def _execute_voice_command_plan(
    episode: Episode,
    plan: VoiceCommandPlan,
) -> tuple[Episode, dict[str, object] | None]:
    updated_episode = episode
    edit_plan_workspace: dict[str, object] | None = None
    for operation in plan.operations:
        if operation.kind == "generate_topics":
            updated_episode = workflow.run(
                "generate_topics",
                updated_episode,
                feedback_history=feedback_history_for(updated_episode.id),
            )
        elif operation.kind == "regenerate_topics":
            updated_episode = workflow.run(
                "regenerate_topics",
                updated_episode,
                feedback_history=feedback_history_for(updated_episode.id),
            )
        elif operation.kind == "generate_script":
            updated_episode = workflow.run(
                "generate_script",
                updated_episode,
                feedback_history=feedback_history_for(updated_episode.id),
            )
        elif operation.kind == "regenerate_script":
            updated_episode = workflow.run(
                "regenerate_script",
                updated_episode,
                feedback_history=feedback_history_for(updated_episode.id),
                reason=_string_param(operation.params, "reason"),
            )
        elif operation.kind == "regenerate_script_section":
            updated_episode = workflow.run(
                "regenerate_script_section",
                updated_episode,
                section_id=_required_string_param(operation.params, "section_id"),
                feedback_history=feedback_history_for(updated_episode.id),
                reason=_string_param(operation.params, "reason"),
            )
        elif operation.kind == "approve_script":
            updated_episode = workflow.run("approve_script", updated_episode)
        elif operation.kind == "create_draft":
            updated_episode = _create_draft_from_voice_command(updated_episode)
        elif operation.kind == "edit_plan_segment_decision":
            edit_plan_workspace = update_edit_plan_segment_decision(
                updated_episode,
                settings.video_output_path,
                segment_id=_required_string_param(operation.params, "segment_id"),
                action=_required_string_param(operation.params, "action"),
                reason=_string_param(operation.params, "reason"),
            )
        else:
            raise ValueError(f"unsupported voice command operation: {operation.kind}")
    return repo.save(updated_episode), edit_plan_workspace


def _create_draft_from_voice_command(episode: Episode) -> Episode:
    if not episode.video_processed_file_name:
        raise ValueError("process a video first")
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
    return workflow.run(
        "bind_draft_package",
        episode,
        package_name=package.package_name,
        package_path=package.path,
    )


def _required_string_param(params: dict[str, object], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"voice command operation missing param: {key}")
    return value


def _string_param(params: dict[str, object], key: str) -> str | None:
    value = params.get(key)
    return value if isinstance(value, str) and value else None


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
def retry_source_sync(key: str) -> SourceSyncRetryResponse:
    """Re-sync a specific source type and return item count + status."""
    known_sources = {"hermes_radar", "manual", "external_urls"}
    if key not in known_sources:
        raise HTTPException(status_code=404, detail=f"Unknown source key: {key}")

    try:
        if key == "hermes_radar":
            items = HermesRadarSourceClient(settings.hermes_radar_path).fetch_items()
            return SourceSyncRetryResponse(
                source_type=key,
                status="success",
                message=f"Hermes radar re-synced: {len(items)} items",
                item_count=len(items),
            )
        elif key == "external_urls":
            if not settings.topic_source_urls:
                return SourceSyncRetryResponse(
                    source_type=key,
                    status="skipped",
                    message="No external source URLs configured",
                )
            items = HtmlPageSourceClient(settings.topic_source_urls).fetch_items()
            return SourceSyncRetryResponse(
                source_type=key,
                status="success",
                message=f"External URLs re-synced: {len(items)} items",
                item_count=len(items),
            )
        elif key == "manual":
            return SourceSyncRetryResponse(
                source_type=key,
                status="success",
                message=f"Manual sources: {len(manual_source_items)} items in memory",
                item_count=len(manual_source_items),
            )
    except ExternalSourceResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Source retry failed for {key}: {exc}",
        ) from exc

    # Unreachable: unknown source caught above with 404
    raise HTTPException(status_code=404, detail=f"Unknown source key: {key}")


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
