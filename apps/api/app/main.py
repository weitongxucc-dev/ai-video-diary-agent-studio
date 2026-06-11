from __future__ import annotations

import logging
import sys
from types import ModuleType

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.asr import asr_transcriber_from_settings
from app.config import Settings, runtime_env_file_path
from app.creator_profile import (
    CreatorProfileRepository,
)
from app.drafts import DraftWorkspace
from app import episode_media_flow
from app import episode_workflow_flow
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
from app.routers import creator_profile as creator_profile_router
from app.routers import drafts as drafts_router
from app.routers import edit_plan as edit_plan_router
from app.routers import episode_media as episode_media_router
from app.routers import episode_workflow as episode_workflow_router
from app.routers import feedback as feedback_router
from app.routers import knowledge as knowledge_router
from app.routers import preferences as preferences_router
from app.routers import retake_reports as retake_reports_router
from app.routers import settings as settings_router
from app.routers import sources as sources_router
from app.routers import system as system_router
from app.routers import videos as videos_router
from app.routers.context import AppContext, AppState
from app.runtime import RUNTIME_DEPENDENCY_NAMES, RuntimeContainer, create_runtime
from app.schemas import (
    EpisodeResponse,
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


def _install_runtime(runtime: RuntimeContainer | None = None) -> RuntimeContainer:
    runtime = runtime or create_runtime()
    app.state.runtime = runtime
    return runtime


def get_app_runtime() -> RuntimeContainer:
    return app.state.runtime


def set_runtime_dependency(name: str, value) -> RuntimeContainer:
    runtime = get_app_runtime().with_dependency(name, value)
    app.state.runtime = runtime
    return runtime


_install_runtime()


def get_episode_or_404(episode_id: str) -> Episode:
    try:
        return get_app_context().repo.get(episode_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc


def episode_response(episode: Episode) -> EpisodeResponse:
    return EpisodeResponse.model_validate(
        recover_interrupted_video_job_if_needed(episode),
        from_attributes=True,
    )


def creator_profile_response() -> CreatorProfileResponse:
    profile = get_app_context().creator_profile_repo.load()
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
    current_runtime = get_app_runtime()
    _install_runtime(
        create_runtime(
            runtime_env_path=current_runtime.runtime_env_path,
            app_state=current_runtime.app_state,
            video_job_runner=current_runtime.video_job_runner,
            edit_plan_apply_runner=current_runtime.edit_plan_apply_runner,
        )
    )


def get_app_context() -> AppContext:
    runtime = get_app_runtime()
    return AppContext(
        settings=runtime.settings,
        repo=runtime.repo,
        creator_profile_repo=runtime.creator_profile_repo,
        preference_repo=runtime.preference_repo,
        vault=runtime.vault,
        service=runtime.service,
        workflow=runtime.workflow,
        manual_source_items=runtime.app_state.manual_source_items,
        hermes_source_client_factory=HermesRadarSourceClient,
        external_source_client_factory=HtmlPageSourceClient,
        runtime_env_path=runtime.runtime_env_path,
        reload_runtime_dependencies=_reload_runtime_dependencies,
        video_inbox=runtime.video_inbox,
        video_pipeline=runtime.video_pipeline,
        draft_workspace=runtime.draft_workspace,
        video_job_runner=runtime.video_job_runner,
        edit_plan_apply_runner=runtime.edit_plan_apply_runner,
        voice_transcriber=runtime.voice_transcriber,
    )


app.include_router(system_router.create_router(get_app_context))
app.include_router(diagnostics_router.create_router(get_app_context))
app.include_router(settings_router.create_router(get_app_context))
app.include_router(sources_router.create_router(get_app_context))
app.include_router(creator_profile_router.create_router(get_app_context))
app.include_router(knowledge_router.create_router(get_app_context))
app.include_router(feedback_router.create_router(get_app_context))
app.include_router(preferences_router.create_router(get_app_context))
app.include_router(episode_workflow_router.create_router(get_app_context))
app.include_router(cover_router.create_router(get_app_context))
app.include_router(retake_reports_router.create_router(get_app_context))
app.include_router(edit_plan_router.create_router(get_app_context))
app.include_router(videos_router.create_router(get_app_context))
app.include_router(drafts_router.create_router(get_app_context))
app.include_router(episode_media_router.create_router(get_app_context))


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


class _RuntimeBackedModule(ModuleType):
    def __getattr__(self, name: str):
        if name in RUNTIME_DEPENDENCY_NAMES:
            return getattr(get_app_runtime(), name)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value) -> None:
        if name in RUNTIME_DEPENDENCY_NAMES:
            set_runtime_dependency(name, value)
            return
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _RuntimeBackedModule
