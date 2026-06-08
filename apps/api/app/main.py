from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from pathlib import Path

from app.asr import asr_transcriber_from_settings
from app.config import Settings, runtime_env_file_path
from app.creator_profile import (
    CreatorProfileRepository,
    creator_profile_prompt_payload,
)
from app.drafts import DraftWorkspace
from app import episode_media_flow
from app import episode_workflow_flow
from app.edit_plan import (
    build_edit_plan_workspace,
    revert_edit_plan_segment_decision,
    update_edit_plan_segment_decision,
)
from app.external_sources import HtmlPageSourceClient
from app.hermes_radar import HermesRadarSourceClient
from app.llm import script_generator_from_settings
from app.models import (
    Episode,
    EpisodeStatus,
    VideoJobStatus,
)
from app.preference_memory import (
    LongTermPreferenceRepository,
)
from app.repository import EpisodeRepository
from app.routers import diagnostics as diagnostics_router
from app.routers import cover as cover_router
from app.routers import drafts as drafts_router
from app.routers import episode_media as episode_media_router
from app.routers import episode_workflow as episode_workflow_router
from app.routers import feedback as feedback_router
from app.routers import preferences as preferences_router
from app.routers import settings as settings_router
from app.routers import sources as sources_router
from app.routers import system as system_router
from app.routers import videos as videos_router
from app.routers.context import AppContext, AppState
from app.runtime_settings import is_local_client_host
from app.schemas import (
    CreatorProfileResponse,
    CreatorProfileUpdateRequest,
    EditPlanResponse,
    EditPlanSegmentDecisionRequest,
    EpisodeResponse,
    KnowledgeNoteResponse,
    RetakeReportResponse,
)
from app.services import EpisodeService
from app.sources import ObsidianVault
from app.topics import (
    topic_generator_from_settings,
)
from app.video import FfmpegVideoProcessor, VideoInbox
from app.video_jobs import VideoJobRunner
from app.video_pipeline import video_processing_pipeline_from_settings
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
app_state = AppState()


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
    return episode_media_flow.recover_interrupted_video_job_if_needed(
        get_app_context(),
        episode,
    )


def feedback_history_for(episode_id: str):
    return episode_workflow_flow.feedback_history_for(get_app_context(), episode_id)


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


def get_app_context() -> AppContext:
    return AppContext(
        settings=settings,
        repo=repo,
        creator_profile_repo=creator_profile_repo,
        preference_repo=preference_repo,
        service=service,
        workflow=workflow,
        manual_source_items=app_state.manual_source_items,
        hermes_source_client_factory=HermesRadarSourceClient,
        external_source_client_factory=HtmlPageSourceClient,
        runtime_env_path=runtime_env_path,
        reload_runtime_dependencies=_reload_runtime_dependencies,
        video_inbox=video_inbox,
        video_pipeline=video_pipeline,
        draft_workspace=draft_workspace,
        video_job_runner=video_job_runner,
        edit_plan_apply_runner=edit_plan_apply_runner,
        voice_transcriber=voice_transcriber,
    )


app.include_router(system_router.create_router(get_app_context))
app.include_router(diagnostics_router.create_router(get_app_context))
app.include_router(settings_router.create_router(get_app_context))
app.include_router(sources_router.create_router(get_app_context))
app.include_router(feedback_router.create_router(get_app_context))
app.include_router(preferences_router.create_router(get_app_context))
app.include_router(episode_workflow_router.create_router(get_app_context))
app.include_router(cover_router.create_router(get_app_context))
app.include_router(videos_router.create_router(get_app_context))
app.include_router(drafts_router.create_router(get_app_context))
app.include_router(episode_media_router.create_router(get_app_context))


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


def _complete_ad_hoc_episode_video(episode_id: str, file_name: str) -> None:
    episode_media_flow.complete_ad_hoc_episode_video(
        get_app_context,
        episode_id,
        file_name,
        logger=logger,
    )


def _complete_ad_hoc_episode_video_batch(
    episode_id: str,
    file_names: list[str],
) -> None:
    episode_media_flow.complete_ad_hoc_episode_video_batch(
        get_app_context,
        episode_id,
        file_names,
        logger=logger,
    )


def _bind_processed_video_and_draft(episode_id: str, processed) -> None:
    episode_media_flow.bind_processed_video_and_draft(
        get_app_context(),
        episode_id,
        processed,
    )


def _start_video_job(episode: Episode) -> None:
    episode_media_flow.start_video_job(episode)


def _mark_video_job_stage(
    episode_id: str,
    status: VideoJobStatus,
    message: str,
) -> None:
    episode_media_flow.mark_video_job_stage(
        get_app_context(),
        episode_id,
        status,
        message,
    )


def _mark_video_job_completed(episode_id: str) -> None:
    episode_media_flow.mark_video_job_completed(get_app_context(), episode_id)


def _mark_video_job_failed(episode_id: str, error: Exception) -> None:
    episode_media_flow.mark_video_job_failed(get_app_context(), episode_id, error)
