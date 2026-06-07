from __future__ import annotations

from datetime import date, datetime, time
import time as time_module
from typing import Callable, Protocol

from app.config import Settings
from app.creator_profile import CreatorProfile, CreatorProfileRepository
from app.drafts import DraftWorkspace
from app.llm import script_generator_from_settings
from app.repository import EpisodeRepository
from app.services import EpisodeService
from app.sources import ObsidianVault
from app.topics import topic_generator_from_settings
from app.video import ProcessedVideo, VideoInbox
from app.video_pipeline import video_processing_pipeline_from_settings
from app.workflow import LangGraphEpisodeWorkflow


class VideoProcessor(Protocol):
    def process(self, file_name: str) -> ProcessedVideo: ...

    def process_batch(self, file_names: list[str]) -> ProcessedVideo: ...


class AgentAutomationWorker:
    def __init__(
        self,
        repo: EpisodeRepository,
        workflow: LangGraphEpisodeWorkflow,
        video_inbox: VideoInbox | None = None,
        video_processor: VideoProcessor | None = None,
        draft_workspace: DraftWorkspace | None = None,
        daily_topic_time: time = time(7, 0),
        creator_profile_provider: Callable[[], CreatorProfile] | None = None,
    ) -> None:
        self.repo = repo
        self.workflow = workflow
        self.video_inbox = video_inbox
        self.video_processor = video_processor
        self.draft_workspace = draft_workspace
        self.daily_topic_time = daily_topic_time
        self.creator_profile_provider = creator_profile_provider
        self._topic_runs: set[date] = set()

    def run_scheduled_topics_once(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        today = now.date()
        if now.time() < self.daily_topic_time or today in self._topic_runs:
            return False
        episode = self.repo.today()
        workflow_kwargs = {}
        if self.creator_profile_provider is not None:
            workflow_kwargs["creator_profile"] = self.creator_profile_provider()
        episode = self.workflow.run("generate_topics", episode, **workflow_kwargs)
        self.repo.save(episode)
        self._topic_runs.add(today)
        return True

    def process_pending_videos_once(self) -> list[str]:
        if not (self.video_inbox and self.video_processor and self.draft_workspace):
            return []
        episode = self.repo.today()
        if episode.script is None:
            return []
        processed_names: list[str] = []
        for video in self.video_inbox.pending_videos():
            if episode.video_processed_file_name:
                break
            episode = self.workflow.run("bind_video", episode, video=video)
            processed = self.video_processor.process(video.file_name)
            episode = self.workflow.run("bind_processed_video", episode, video=processed)
            package = self.draft_workspace.create_package(
                video_file_name=processed.file_name,
                episode=episode,
            )
            episode = self.workflow.run(
                "bind_draft_package",
                episode,
                package_name=package.package_name,
                package_path=package.path,
            )
            self.repo.save(episode)
            processed_names.append(video.file_name)
        return processed_names

    def run_forever(self, poll_interval_seconds: float = 30.0) -> None:
        while True:
            self.run_scheduled_topics_once()
            self.process_pending_videos_once()
            time_module.sleep(poll_interval_seconds)


def build_worker_from_env() -> AgentAutomationWorker:
    settings = Settings.from_env()
    vault = ObsidianVault(settings.obsidian_vault_path)
    creator_profile_repo = CreatorProfileRepository(settings.database_path)
    service = EpisodeService(
        script_generator=script_generator_from_settings(settings),
        topic_generator=topic_generator_from_settings(settings, vault),
    )
    return AgentAutomationWorker(
        repo=EpisodeRepository(database_path=settings.database_path),
        workflow=LangGraphEpisodeWorkflow(service),
        video_inbox=VideoInbox(settings.video_inbox_path),
        video_processor=video_processing_pipeline_from_settings(settings),
        draft_workspace=DraftWorkspace(settings),
        creator_profile_provider=creator_profile_repo.load,
    )


def main() -> None:
    build_worker_from_env().run_forever()


if __name__ == "__main__":
    main()
