from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from types import ModuleType
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app import episode_media_flow, episode_workflow_flow
from app.asr import asr_transcriber_from_settings
from app.config import Settings, runtime_env_file_path
from app.creator_profile import CreatorProfileRepository
from app.drafts import DraftWorkspace
from app.external_sources import HtmlPageSourceClient
from app.hermes_radar import HermesRadarSourceClient
from app.llm import script_generator_from_settings
from app.models import Episode, EpisodeStatus, GenerationJobStatus, VideoJobStatus
from app.preference_memory import LongTermPreferenceRepository
from app.repository import EpisodeRepository
from app.routers import cover as cover_router
from app.routers import creator_profile as creator_profile_router
from app.routers import diagnostics as diagnostics_router
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
from app.runtime_settings import is_local_client_host
from app.services import EpisodeService
from app.sources import ObsidianVault
from app.topics import topic_generator_from_settings
from app.video import FfmpegVideoProcessor, VideoInbox
from app.video_jobs import VideoJobRunner
from app.video_pipeline import video_processing_pipeline_from_settings
from app.workflow import LangGraphEpisodeWorkflow


logger = logging.getLogger(__name__)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def create_app(runtime: RuntimeContainer | None = None) -> FastAPI:
    app = FastAPI(title="AI视频日记 Agent Studio API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:3001"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_api_security_middleware(app)
    install_runtime(app, runtime)
    include_routers(app)
    return app


def install_api_security_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def api_security_middleware(request: Request, call_next):
        if _requires_local_client(request) and not _is_local_request(request):
            return _with_security_headers(
                JSONResponse(
                    status_code=403,
                    content={"detail": "API write requests require a local client"},
                )
            )

        response = await call_next(request)
        return _with_security_headers(response)


def _requires_local_client(request: Request) -> bool:
    return request.method.upper() in WRITE_METHODS and request.url.path.startswith("/api/")


def _is_local_request(request: Request) -> bool:
    client_host = request.client.host if request.client else None
    return is_local_client_host(client_host)


def _with_security_headers(response: Response) -> Response:
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


def install_runtime(
    app: FastAPI,
    runtime: RuntimeContainer | None = None,
) -> RuntimeContainer:
    runtime = runtime or create_runtime()
    app.state.runtime = runtime
    return runtime


def get_app_runtime(app: FastAPI) -> RuntimeContainer:
    return app.state.runtime


def set_runtime_dependency(
    app: FastAPI,
    name: str,
    value: Any,
) -> RuntimeContainer:
    runtime = get_app_runtime(app).with_dependency(name, value)
    app.state.runtime = runtime
    return runtime


def reload_runtime_dependencies(app: FastAPI) -> None:
    current_runtime = get_app_runtime(app)
    install_runtime(
        app,
        create_runtime(
            runtime_env_path=current_runtime.runtime_env_path,
            app_state=current_runtime.app_state,
            video_job_runner=current_runtime.video_job_runner,
            topic_job_runner=current_runtime.topic_job_runner,
            edit_plan_apply_runner=current_runtime.edit_plan_apply_runner,
        ),
    )


def get_app_context(app: FastAPI) -> AppContext:
    runtime = get_app_runtime(app)
    return AppContext(
        settings=runtime.settings,
        repo=runtime.repo,
        creator_profile_repo=runtime.creator_profile_repo,
        preference_repo=runtime.preference_repo,
        vault=runtime.vault,
        service=runtime.service,
        workflow=runtime.workflow,
        manual_source_items=runtime.app_state.manual_source_items,
        hermes_source_client_factory=_state_value(
            app,
            "hermes_source_client_factory",
            HermesRadarSourceClient,
        ),
        external_source_client_factory=_state_value(
            app,
            "external_source_client_factory",
            HtmlPageSourceClient,
        ),
        runtime_env_path=runtime.runtime_env_path,
        reload_runtime_dependencies=lambda: reload_runtime_dependencies(app),
        video_inbox=runtime.video_inbox,
        video_pipeline=runtime.video_pipeline,
        draft_workspace=runtime.draft_workspace,
        video_job_runner=runtime.video_job_runner,
        topic_job_runner=runtime.topic_job_runner,
        edit_plan_apply_runner=runtime.edit_plan_apply_runner,
        voice_transcriber=runtime.voice_transcriber,
    )


def include_routers(app: FastAPI) -> None:
    get_context = lambda: get_app_context(app)
    app.include_router(system_router.create_router(get_context))
    app.include_router(diagnostics_router.create_router(get_context))
    app.include_router(settings_router.create_router(get_context))
    app.include_router(sources_router.create_router(get_context))
    app.include_router(creator_profile_router.create_router(get_context))
    app.include_router(knowledge_router.create_router(get_context))
    app.include_router(feedback_router.create_router(get_context))
    app.include_router(preferences_router.create_router(get_context))
    app.include_router(episode_workflow_router.create_router(get_context))
    app.include_router(cover_router.create_router(get_context))
    app.include_router(retake_reports_router.create_router(get_context))
    app.include_router(edit_plan_router.create_router(get_context))
    app.include_router(videos_router.create_router(get_context))
    app.include_router(drafts_router.create_router(get_context))
    app.include_router(episode_media_router.create_router(get_context))


def get_episode_or_404(app: FastAPI, episode_id: str) -> Episode:
    try:
        return get_app_context(app).repo.get(episode_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="episode not found") from exc


def episode_response(app: FastAPI, episode: Episode):
    return episode_media_flow.episode_response(
        get_app_context(app),
        recover_interrupted_video_job_if_needed(app, episode),
    )


def recover_interrupted_video_job_if_needed(
    app: FastAPI,
    episode: Episode,
) -> Episode:
    return episode_media_flow.recover_interrupted_video_job_if_needed(
        get_app_context(app),
        episode,
    )


def feedback_history_for(app: FastAPI, episode_id: str):
    return episode_workflow_flow.feedback_history_for(get_app_context(app), episode_id)


def complete_ad_hoc_episode_video(
    app: FastAPI,
    episode_id: str,
    file_name: str,
) -> None:
    episode_media_flow.complete_ad_hoc_episode_video(
        lambda: get_app_context(app),
        episode_id,
        file_name,
        logger=logger,
    )


def complete_ad_hoc_episode_video_batch(
    app: FastAPI,
    episode_id: str,
    file_names: list[str],
) -> None:
    episode_media_flow.complete_ad_hoc_episode_video_batch(
        lambda: get_app_context(app),
        episode_id,
        file_names,
        logger=logger,
    )


def bind_processed_video_and_draft(app: FastAPI, episode_id: str, processed) -> None:
    episode_media_flow.bind_processed_video_and_draft(
        get_app_context(app),
        episode_id,
        processed,
    )


def start_video_job(episode: Episode) -> None:
    episode_media_flow.start_video_job(episode)


def mark_video_job_stage(
    app: FastAPI,
    episode_id: str,
    status: VideoJobStatus,
    message: str,
) -> None:
    episode_media_flow.mark_video_job_stage(
        get_app_context(app),
        episode_id,
        status,
        message,
    )


def mark_video_job_completed(app: FastAPI, episode_id: str) -> None:
    episode_media_flow.mark_video_job_completed(get_app_context(app), episode_id)


def mark_video_job_failed(app: FastAPI, episode_id: str, error: Exception) -> None:
    episode_media_flow.mark_video_job_failed(get_app_context(app), episode_id, error)


def install_runtime_module_proxy(module_name: str, app: FastAPI) -> None:
    module = sys.modules[module_name]

    class RuntimeBackedModule(ModuleType):
        def __getattr__(self, name: str):
            if name in RUNTIME_DEPENDENCY_NAMES:
                return getattr(get_app_runtime(app), name)
            if name in _COMPATIBILITY_EXPORTS:
                return _COMPATIBILITY_EXPORTS[name]
            if name in _COMPATIBILITY_FUNCTIONS:
                return _bound_compatibility_function(app, name)
            if name == "HtmlPageSourceClient":
                return _state_value(app, "external_source_client_factory", HtmlPageSourceClient)
            if name == "HermesRadarSourceClient":
                return _state_value(app, "hermes_source_client_factory", HermesRadarSourceClient)
            if name in {"get_app_context", "get_app_runtime"}:
                return lambda: globals()[name](app)
            raise AttributeError(f"module {module_name!r} has no attribute {name!r}")

        def __setattr__(self, name: str, value) -> None:
            if name in RUNTIME_DEPENDENCY_NAMES:
                set_runtime_dependency(app, name, value)
                return
            if name == "HtmlPageSourceClient":
                app.state.external_source_client_factory = value
                return
            if name == "HermesRadarSourceClient":
                app.state.hermes_source_client_factory = value
                return
            super().__setattr__(name, value)

    module.__class__ = RuntimeBackedModule


def _bound_compatibility_function(app: FastAPI, name: str) -> Callable[..., Any]:
    if name == "_complete_ad_hoc_episode_video":
        return lambda episode_id, file_name: complete_ad_hoc_episode_video(
            app,
            episode_id,
            file_name,
        )
    if name == "_complete_ad_hoc_episode_video_batch":
        return lambda episode_id, file_names: complete_ad_hoc_episode_video_batch(
            app,
            episode_id,
            file_names,
        )
    if name == "_bind_processed_video_and_draft":
        return lambda episode_id, processed: bind_processed_video_and_draft(
            app,
            episode_id,
            processed,
        )
    if name == "_mark_video_job_stage":
        return lambda episode_id, status, message: mark_video_job_stage(
            app,
            episode_id,
            status,
            message,
        )
    if name == "_mark_video_job_completed":
        return lambda episode_id: mark_video_job_completed(app, episode_id)
    if name == "_mark_video_job_failed":
        return lambda episode_id, error: mark_video_job_failed(app, episode_id, error)
    if name == "_reload_runtime_dependencies":
        return lambda: reload_runtime_dependencies(app)
    if name == "get_episode_or_404":
        return lambda episode_id: get_episode_or_404(app, episode_id)
    if name == "episode_response":
        return lambda episode: episode_response(app, episode)
    if name == "recover_interrupted_video_job_if_needed":
        return lambda episode: recover_interrupted_video_job_if_needed(app, episode)
    if name == "feedback_history_for":
        return lambda episode_id: feedback_history_for(app, episode_id)
    if name == "_start_video_job":
        return start_video_job
    raise AttributeError(name)


def _state_value(app: FastAPI, name: str, default: Any) -> Any:
    return getattr(app.state, name, default)


_COMPATIBILITY_EXPORTS = {
    "Settings": Settings,
    "CreatorProfileRepository": CreatorProfileRepository,
    "DraftWorkspace": DraftWorkspace,
    "Episode": Episode,
    "EpisodeStatus": EpisodeStatus,
    "GenerationJobStatus": GenerationJobStatus,
    "VideoJobStatus": VideoJobStatus,
    "LongTermPreferenceRepository": LongTermPreferenceRepository,
    "EpisodeRepository": EpisodeRepository,
    "AppContext": AppContext,
    "AppState": AppState,
    "EpisodeService": EpisodeService,
    "ObsidianVault": ObsidianVault,
    "FfmpegVideoProcessor": FfmpegVideoProcessor,
    "VideoInbox": VideoInbox,
    "VideoJobRunner": VideoJobRunner,
    "LangGraphEpisodeWorkflow": LangGraphEpisodeWorkflow,
    "video_processing_pipeline_from_settings": video_processing_pipeline_from_settings,
    "script_generator_from_settings": script_generator_from_settings,
    "topic_generator_from_settings": topic_generator_from_settings,
    "asr_transcriber_from_settings": asr_transcriber_from_settings,
    "runtime_env_file_path": runtime_env_file_path,
}

_COMPATIBILITY_FUNCTIONS = {
    "_bind_processed_video_and_draft",
    "_complete_ad_hoc_episode_video",
    "_complete_ad_hoc_episode_video_batch",
    "_mark_video_job_completed",
    "_mark_video_job_failed",
    "_mark_video_job_stage",
    "_reload_runtime_dependencies",
    "_start_video_job",
    "episode_response",
    "feedback_history_for",
    "get_episode_or_404",
    "recover_interrupted_video_job_if_needed",
}
