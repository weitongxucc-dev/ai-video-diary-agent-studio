from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.asr import asr_transcriber_from_settings
from app.config import Settings, runtime_env_file_path
from app.creator_profile import CreatorProfileRepository
from app.drafts import DraftWorkspace
from app.external_sources import HtmlPageSourceClient
from app.hermes_radar import HermesRadarSourceClient
from app.llm import script_generator_from_settings
from app.preference_memory import LongTermPreferenceRepository
from app.repository import EpisodeRepository
from app.routers.context import AppContext, AppState
from app.services import EpisodeService
from app.sources import ObsidianVault
from app.topics import topic_generator_from_settings
from app.video import FfmpegVideoProcessor, VideoInbox
from app.video_jobs import VideoJobRunner
from app.video_pipeline import video_processing_pipeline_from_settings
from app.workflow import LangGraphEpisodeWorkflow


@dataclass(frozen=True)
class RuntimeContainer:
    settings: Settings
    repo: EpisodeRepository
    creator_profile_repo: CreatorProfileRepository
    preference_repo: LongTermPreferenceRepository
    vault: ObsidianVault
    video_inbox: VideoInbox
    video_processor: FfmpegVideoProcessor
    video_pipeline: Any
    draft_workspace: DraftWorkspace
    voice_transcriber: Any
    runtime_env_path: Path
    service: EpisodeService
    workflow: LangGraphEpisodeWorkflow
    video_job_runner: VideoJobRunner
    edit_plan_apply_runner: Any
    app_state: AppState

    def with_dependency(self, name: str, value: Any) -> RuntimeContainer:
        if name not in RUNTIME_DEPENDENCY_NAMES:
            raise AttributeError(name)
        return replace(self, **{name: value})


RUNTIME_DEPENDENCY_NAMES = frozenset(RuntimeContainer.__dataclass_fields__)


def create_runtime(
    *,
    settings: Settings | None = None,
    runtime_env_path: str | Path | None = None,
    app_state: AppState | None = None,
    video_job_runner: VideoJobRunner | None = None,
    edit_plan_apply_runner: Any = None,
) -> RuntimeContainer:
    settings = settings or Settings.from_env()
    video_processor = FfmpegVideoProcessor(settings)
    vault = ObsidianVault(settings.obsidian_vault_path)
    service = EpisodeService(
        script_generator=script_generator_from_settings(settings),
        topic_generator=topic_generator_from_settings(settings, vault),
    )
    return RuntimeContainer(
        settings=settings,
        repo=EpisodeRepository(database_path=settings.database_path),
        creator_profile_repo=CreatorProfileRepository(settings.database_path),
        preference_repo=LongTermPreferenceRepository(settings.database_path),
        vault=vault,
        video_inbox=VideoInbox(settings.video_inbox_path),
        video_processor=video_processor,
        video_pipeline=video_processing_pipeline_from_settings(
            settings,
            processor=video_processor,
        ),
        draft_workspace=DraftWorkspace(settings),
        voice_transcriber=asr_transcriber_from_settings(settings),
        runtime_env_path=Path(runtime_env_path or runtime_env_file_path()),
        service=service,
        workflow=LangGraphEpisodeWorkflow(service),
        video_job_runner=video_job_runner or VideoJobRunner(),
        edit_plan_apply_runner=edit_plan_apply_runner,
        app_state=app_state or AppState(),
    )


def runtime_context(
    runtime: RuntimeContainer,
    *,
    reload_runtime_dependencies: Any,
) -> AppContext:
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
        reload_runtime_dependencies=reload_runtime_dependencies,
        video_inbox=runtime.video_inbox,
        video_pipeline=runtime.video_pipeline,
        draft_workspace=runtime.draft_workspace,
        video_job_runner=runtime.video_job_runner,
        edit_plan_apply_runner=runtime.edit_plan_apply_runner,
        voice_transcriber=runtime.voice_transcriber,
    )
