from __future__ import annotations

from collections import Counter
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from pathlib import Path

from app.asr import (
    ASRConfigurationError,
    ASRTranscriptionError,
    asr_transcriber_from_settings,
)
from app.config import Settings, runtime_env_file_path
from app.comment_insights import extract_comment_insights
from app.content_templates import get_content_template, list_content_templates
from app.creator_profile import (
    CreatorProfileRepository,
    creator_profile_prompt_payload,
)
from app.diagnostics import diagnostics_report_bytes, diagnostics_status
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
from app.llm import LLMConfigurationError, LLMResponseError, script_generator_from_settings
from app.models import (
    Episode,
    EpisodeStatus,
    FeedbackEvent,
    FeedbackValue,
    ScriptPackage,
    ScriptSection,
    VideoJobStatus,
    utc_now,
)
from app.preference_memory import (
    LongTermPreferenceRepository,
    build_preference_candidates,
    build_preference_memory,
    long_term_preference_events,
)
from app.repository import EpisodeRepository
from app.runtime_health import runtime_health
from app.runtime_settings import apply_runtime_settings_update, is_local_client_host
from app.schemas import (
    ApproveTopicRequest,
    CommentInsightsRequest,
    CommentInsightsResponse,
    ContentTemplateSelectRequest,
    ContentTemplatesResponse,
    CreatorProfileResponse,
    CreatorProfileUpdateRequest,
    DiagnosticsStatusResponse,
    DraftPackageRequest,
    DraftPackageResponse,
    EditPlanResponse,
    EditPlanSegmentDecisionRequest,
    DraftImportRequest,
    DraftImportResponse,
    DraftAssetResponse,
    DraftEnvironmentResponse,
    DraftStatusResponse,
    EpisodeResponse,
    FeedbackRequest,
    FeedbackEventResponse,
    FeedbackRevertRequest,
    FeedbackSummaryResponse,
    KnowledgeNoteResponse,
    MarkPublishedRequest,
    ManualLinkRequest,
    PendingVideoResponse,
    PreferencesResponse,
    PreferenceConfirmRequest,
    PreferenceItemResponse,
    ProcessedVideoResponse,
    ProcessVideoBatchRequest,
    ProcessVideoRequest,
    RetakeReportResponse,
    RuntimeSettingsResponse,
    RuntimeSettingsUpdateRequest,
    RuntimeHealthResponse,
    ScriptRegenerateRequest,
    ScriptSectionRegenerateRequest,
    ScriptUpdateRequest,
    ScriptVersionRevertRequest,
    SourceItemsResponse,
    SourceItemResponse,
    SystemStatusResponse,
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
from app.video import FfmpegVideoProcessor, VideoInbox
from app.video_jobs import VideoJobRunner
from app.video_pipeline import video_processing_pipeline_from_settings
from app.voice_commands import VoiceCommandPlan, parse_voice_command
from app.voice_transcription import transcribe_voice_audio
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

settings = Settings.from_env()
repo = EpisodeRepository(database_path=settings.database_path)
creator_profile_repo = CreatorProfileRepository(settings.database_path)
preference_repo = LongTermPreferenceRepository(settings.database_path)
vault = ObsidianVault(settings.obsidian_vault_path)
video_inbox = VideoInbox(settings.video_inbox_path)
video_processor = FfmpegVideoProcessor(settings)
video_pipeline = video_processing_pipeline_from_settings(settings, processor=video_processor)
draft_workspace = DraftWorkspace(settings)
voice_transcriber = asr_transcriber_from_settings(settings)
runtime_env_path = runtime_env_file_path()
service = EpisodeService(
    script_generator=script_generator_from_settings(settings),
    topic_generator=topic_generator_from_settings(settings, vault),
)
workflow = LangGraphEpisodeWorkflow(service)
video_job_runner = VideoJobRunner()
edit_plan_apply_runner = None
manual_source_items: list[TopicSourceItem] = []


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


def creator_profile_response() -> CreatorProfileResponse:
    profile = creator_profile_repo.load()
    return CreatorProfileResponse.model_validate(
        {
            **profile.__dict__,
            "prompt_payload": creator_profile_prompt_payload(profile),
        }
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
    blocked_preference_keys = {
        (preference.kind, preference.content)
        for preference in preference_repo.all()
        if preference.deleted or not preference.enabled
    }
    episode_feedback = [
        event
        for episode in repo.all()
        if episode.id != episode_id
        for event in episode.feedback_events
        if event.active
        and _feedback_preference_key(event) not in blocked_preference_keys
    ][-30:]
    return [
        *episode_feedback,
        *long_term_preference_events(preference_repo.active()),
    ]


def _feedback_preference_key(event: FeedbackEvent) -> tuple[str, str] | None:
    if not event.reason:
        return None
    kind = "prefer" if event.value == FeedbackValue.UP else "avoid"
    return (kind, event.reason.strip())


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


@app.get("/api/runtime/health", response_model=RuntimeHealthResponse)
def get_runtime_health() -> RuntimeHealthResponse:
    return RuntimeHealthResponse.model_validate(runtime_health(settings))


@app.get("/api/diagnostics/status", response_model=DiagnosticsStatusResponse)
def get_diagnostics_status() -> DiagnosticsStatusResponse:
    return DiagnosticsStatusResponse.model_validate(diagnostics_status(settings, repo))


@app.get("/api/diagnostics/report")
def download_diagnostics_report() -> Response:
    return Response(
        content=diagnostics_report_bytes(settings, repo),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="ai-video-diary-diagnostics.zip"'
        },
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


def _reload_runtime_dependencies() -> None:
    global settings, repo, creator_profile_repo, preference_repo, vault, video_inbox, video_processor, video_pipeline, draft_workspace, voice_transcriber, service, workflow

    settings = Settings.from_env()
    repo = EpisodeRepository(database_path=settings.database_path)
    creator_profile_repo = CreatorProfileRepository(settings.database_path)
    preference_repo = LongTermPreferenceRepository(settings.database_path)
    vault = ObsidianVault(settings.obsidian_vault_path)
    video_inbox = VideoInbox(settings.video_inbox_path)
    video_processor = FfmpegVideoProcessor(settings)
    video_pipeline = video_processing_pipeline_from_settings(
        settings,
        processor=video_processor,
    )
    draft_workspace = DraftWorkspace(settings)
    voice_transcriber = asr_transcriber_from_settings(settings)
    service = EpisodeService(
        script_generator=script_generator_from_settings(settings),
        topic_generator=topic_generator_from_settings(settings, vault),
    )
    workflow = LangGraphEpisodeWorkflow(service)


@app.get("/api/creator-profile", response_model=CreatorProfileResponse)
def get_creator_profile() -> CreatorProfileResponse:
    return creator_profile_response()


@app.post("/api/creator-profile", response_model=CreatorProfileResponse)
def update_creator_profile(
    request: Request,
    payload: CreatorProfileUpdateRequest,
) -> CreatorProfileResponse:
    client_host = request.client.host if request.client else None
    if not is_local_client_host(client_host):
        raise HTTPException(
            status_code=403,
            detail="creator profile can only be updated from a local client",
        )
    creator_profile_repo.save(payload.model_dump(exclude_none=True))
    return creator_profile_response()


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


@app.get("/api/feedback/summary", response_model=FeedbackSummaryResponse)
def get_feedback_summary() -> FeedbackSummaryResponse:
    all_event_pairs = [
        (episode.id, event)
        for episode in repo.all()
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
def get_preferences() -> PreferencesResponse:
    events = [
        event
        for episode in repo.all()
        for event in episode.feedback_events
        if event.reason and event.active
    ]
    long_term_preferences = preference_repo.all()
    active_memory_events = long_term_preference_events(preference_repo.active())
    memory = build_preference_memory([*events, *active_memory_events], limit=20)
    confirmed_keys = {
        (preference.kind, preference.content)
        for preference in long_term_preferences
        if not preference.deleted
    }
    candidates = build_preference_candidates(events, limit=20)
    preferences = [
        PreferenceItemResponse(
            label=label,
            signal=signal.signal,
            evidence_count=signal.evidence_count,
            confidence=signal.confidence,
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
        candidates=[
            {
                **candidate.__dict__,
                "confirmed": (candidate.kind, candidate.content) in confirmed_keys,
            }
            for candidate in candidates
        ],
        long_term_preferences=[
            _long_term_preference_response(preference)
            for preference in long_term_preferences
        ],
    )


@app.post("/api/preferences/confirm", response_model=PreferencesResponse)
def confirm_preference(request: PreferenceConfirmRequest) -> PreferencesResponse:
    try:
        preference_repo.confirm(
            kind=request.kind,
            content=request.content,
            source_event_ids=request.source_event_ids,
            confidence=request.confidence,
            scope=request.scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_preferences()


@app.post("/api/preferences/{preference_id}/disable", response_model=PreferencesResponse)
def disable_preference(preference_id: str) -> PreferencesResponse:
    try:
        preference_repo.disable(preference_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="preference not found") from exc
    return get_preferences()


@app.post("/api/preferences/{preference_id}/restore", response_model=PreferencesResponse)
def restore_preference(preference_id: str) -> PreferencesResponse:
    try:
        preference_repo.restore(preference_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="preference not found") from exc
    return get_preferences()


@app.post("/api/preferences/{preference_id}/delete", response_model=PreferencesResponse)
def delete_preference(preference_id: str) -> PreferencesResponse:
    try:
        preference_repo.delete(preference_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="preference not found") from exc
    return get_preferences()


def _long_term_preference_response(preference) -> dict[str, object]:
    return {
        "id": preference.id,
        "kind": preference.kind,
        "label": "偏好" if preference.kind == "prefer" else "避开",
        "content": preference.content,
        "source_event_ids": preference.source_event_ids,
        "confidence": preference.confidence,
        "scope": preference.scope,
        "enabled": preference.enabled,
        "deleted": preference.deleted,
        "created_at": preference.created_at,
        "updated_at": preference.updated_at,
        "last_used_at": preference.last_used_at,
    }


@app.get("/api/videos/pending", response_model=list[PendingVideoResponse])
def get_pending_videos() -> list[PendingVideoResponse]:
    return [
        PendingVideoResponse.model_validate(video, from_attributes=True)
        for video in video_inbox.pending_videos()
    ]


@app.post("/api/videos/process", response_model=ProcessedVideoResponse)
def process_video(request: ProcessVideoRequest) -> ProcessedVideoResponse:
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
def create_draft_package(request: DraftPackageRequest) -> DraftPackageResponse:
    try:
        package = draft_workspace.create_package(
            video_file_name=request.video_file_name,
            episode=repo.today(),
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
def get_today_episode() -> EpisodeResponse:
    return episode_response(repo.today())


@app.post("/api/episodes", response_model=EpisodeResponse)
def create_episode() -> EpisodeResponse:
    return episode_response(repo.create())


@app.get("/api/episodes/{episode_id}", response_model=EpisodeResponse)
def get_episode(episode_id: str) -> EpisodeResponse:
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
        episode = workflow.run(
            "generate_topics",
            episode,
            feedback_history=feedback_history_for(episode_id),
            creator_profile=creator_profile_repo.load(),
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
        episode = workflow.run(
            "regenerate_topics",
            episode,
            feedback_history=feedback_history_for(episode_id),
            creator_profile=creator_profile_repo.load(),
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
            creator_profile=creator_profile_repo.load(),
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
        episode = workflow.run(
            "regenerate_script",
            get_episode_or_404(episode_id),
            feedback_history=feedback_history_for(episode_id),
            reason=request.reason,
            creator_profile=creator_profile_repo.load(),
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
        episode = workflow.run(
            "regenerate_script_section",
            get_episode_or_404(episode_id),
            section_id=section_id,
            feedback_history=feedback_history_for(episode_id),
            reason=request.reason,
            creator_profile=creator_profile_repo.load(),
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


@app.post("/api/voice/transcribe", response_model=VoiceTranscriptionResponse)
async def transcribe_voice_command_audio(request: Request) -> VoiceTranscriptionResponse:
    try:
        transcription = transcribe_voice_audio(
            await request.body(),
            content_type=request.headers.get("content-type"),
            transcriber=voice_transcriber,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ASRConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ASRTranscriptionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return VoiceTranscriptionResponse.model_validate(transcription)


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
                creator_profile=creator_profile_repo.load(),
            )
        elif operation.kind == "regenerate_topics":
            updated_episode = workflow.run(
                "regenerate_topics",
                updated_episode,
                feedback_history=feedback_history_for(updated_episode.id),
                creator_profile=creator_profile_repo.load(),
            )
        elif operation.kind == "generate_script":
            updated_episode = workflow.run(
                "generate_script",
                updated_episode,
                feedback_history=feedback_history_for(updated_episode.id),
                creator_profile=creator_profile_repo.load(),
            )
        elif operation.kind == "regenerate_script":
            updated_episode = workflow.run(
                "regenerate_script",
                updated_episode,
                feedback_history=feedback_history_for(updated_episode.id),
                reason=_string_param(operation.params, "reason"),
                creator_profile=creator_profile_repo.load(),
            )
        elif operation.kind == "regenerate_script_section":
            updated_episode = workflow.run(
                "regenerate_script_section",
                updated_episode,
                section_id=_required_string_param(operation.params, "section_id"),
                feedback_history=feedback_history_for(updated_episode.id),
                reason=_string_param(operation.params, "reason"),
                creator_profile=creator_profile_repo.load(),
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
    package = draft_workspace.create_package(
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
    request: ProcessVideoRequest,
) -> EpisodeResponse:
    if not request.file_name:
        raise HTTPException(status_code=400, detail="file_name is required")
    if video_job_runner.is_active(episode_id):
        return episode_response(get_episode_or_404(episode_id))
    try:
        episode = workflow.run(
            "bind_ad_hoc_video",
            get_episode_or_404(episode_id),
            video=_pending_video_by_name(request.file_name),
        )
        _start_video_job(episode)
        episode = repo.save(episode)
        video_job_runner.submit(
            episode_id,
            lambda: _complete_ad_hoc_episode_video(episode_id, request.file_name),
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
    request: ProcessVideoBatchRequest,
) -> EpisodeResponse:
    if video_job_runner.is_active(episode_id):
        return episode_response(get_episode_or_404(episode_id))
    try:
        videos = _pending_videos_by_name(request.file_names)
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
                request.file_names,
            ),
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
    package = draft_workspace.create_package(
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
    episode.video_job_message = "视频已处理，并已生成待确认的剪映草稿素材包"
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
        package = draft_workspace.create_package(
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
        play_count=request.play_count,
        like_count=request.like_count,
        comment_count=request.comment_count,
        feedback_value=request.feedback_value,
        feedback_reason=request.feedback_reason,
    )
    return episode_response(repo.save(episode))


@app.post("/api/episodes/{episode_id}/comment-insights", response_model=CommentInsightsResponse)
def analyze_episode_comment_insights(
    episode_id: str,
    request: CommentInsightsRequest,
) -> CommentInsightsResponse:
    episode = get_episode_or_404(episode_id)
    created_candidates: list[dict[str, object]] = []
    for insight in extract_comment_insights(request.comments_text):
        source_event_ids: list[str] = []
        for source_comment in insight.source_comments:
            if _has_comment_insight_event(episode, insight.content, source_comment):
                continue
            event = FeedbackEvent(
                "published_comment_insight",
                episode.id,
                FeedbackValue.UP if insight.kind == "prefer" else FeedbackValue.DOWN,
                insight.content,
                before_snapshot={"source_comment": source_comment},
                after_snapshot={"comment_insight": insight.content},
            )
            episode.feedback_events.append(event)
            source_event_ids.append(event.id)
        if not source_event_ids:
            continue
        created_candidates.append(
            {
                "kind": insight.kind,
                "label": "偏好" if insight.kind == "prefer" else "避开",
                "content": insight.content,
                "source_event_ids": source_event_ids,
                "evidence_count": len(source_event_ids),
                "confidence": round(min(1.0, 0.35 + len(source_event_ids) * 0.15), 2),
                "scope": "publish",
                "confirmed": False,
            }
        )
    saved = repo.save(episode) if created_candidates else episode
    return CommentInsightsResponse(
        episode=episode_response(saved),
        created_candidates=created_candidates,
    )


def _has_comment_insight_event(
    episode: Episode,
    content: str,
    source_comment: str,
) -> bool:
    for event in episode.feedback_events:
        if event.target_type != "published_comment_insight" or event.reason != content:
            continue
        if not isinstance(event.before_snapshot, dict):
            continue
        if event.before_snapshot.get("source_comment") == source_comment:
            return True
    return False


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
