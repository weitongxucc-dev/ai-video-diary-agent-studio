import json
from datetime import date, datetime, time
from pathlib import Path

import app.automation as automation_module
from app.automation import AgentAutomationWorker
from app.config import Settings
from app.creator_profile import CreatorProfile, CreatorProfileRepository
from app.drafts import DraftToolStatus, DraftWorkspace
from app.llm import TemplateScriptGenerator
from app.models import EpisodeStatus, ScriptPackage, ScriptSection
from app.repository import EpisodeRepository
from app.services import EpisodeService
from app.topics import TemplateTopicGenerator
from app.video import ProcessedVideo, VideoInbox
from app.workflow import LangGraphEpisodeWorkflow


def _service() -> EpisodeService:
    return EpisodeService(
        script_generator=TemplateScriptGenerator(),
        topic_generator=TemplateTopicGenerator(),
    )


class CapturingVideoPipeline:
    def __init__(self, processed: ProcessedVideo) -> None:
        self.processed = processed
        self.process_calls = []
        self.process_batch_calls = []

    def process(self, file_name: str) -> ProcessedVideo:
        self.process_calls.append(file_name)
        return self.processed

    def process_batch(self, file_names: list[str]) -> ProcessedVideo:
        self.process_batch_calls.append(file_names)
        return self.processed


def test_automation_worker_generates_daily_topics_once():
    repo = EpisodeRepository(today_provider=lambda: date(2026, 5, 28))
    worker = AgentAutomationWorker(
        repo=repo,
        workflow=LangGraphEpisodeWorkflow(_service()),
        daily_topic_time=time(7, 0),
    )

    first = worker.run_scheduled_topics_once(datetime(2026, 5, 28, 7, 1))
    second = worker.run_scheduled_topics_once(datetime(2026, 5, 28, 8, 0))

    assert first is True
    assert second is False
    assert repo.today().status == EpisodeStatus.TOPICS_READY
    assert len(repo.today().topics) == 4


def test_automation_worker_passes_creator_profile_to_scheduled_topics():
    class CapturingWorkflow:
        def __init__(self) -> None:
            self.action = None
            self.creator_profile = None

        def run(self, action, episode, **kwargs):
            self.action = action
            self.creator_profile = kwargs.get("creator_profile")
            episode.status = EpisodeStatus.TOPICS_READY
            return episode

    repo = EpisodeRepository(today_provider=lambda: date(2026, 5, 28))
    profile = CreatorProfile(
        identity="技术型个人创作者",
        content_domains=["AI 产品"],
        tone_keywords=["直接"],
    )
    workflow = CapturingWorkflow()
    worker = AgentAutomationWorker(
        repo=repo,
        workflow=workflow,
        creator_profile_provider=lambda: profile,
        daily_topic_time=time(7, 0),
    )

    result = worker.run_scheduled_topics_once(datetime(2026, 5, 28, 7, 1))

    assert result is True
    assert workflow.action == "generate_topics"
    assert workflow.creator_profile is profile


def test_build_worker_from_env_uses_video_processing_pipeline(tmp_path, monkeypatch):
    settings = Settings(
        database_path=str(tmp_path / "agent.sqlite3"),
        video_inbox_path=str(tmp_path / "inbox"),
        video_output_path=str(tmp_path / "processed"),
        draft_output_path=str(tmp_path / "drafts"),
    )
    CreatorProfileRepository(settings.database_path).save(
        {"identity": "定时 worker 画像"}
    )
    pipeline = CapturingVideoPipeline(
        ProcessedVideo("raw_preview.mp4", "/tmp/raw_preview.mp4", "raw.mov")
    )
    captured_settings = []

    monkeypatch.setattr(automation_module.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(
        automation_module,
        "video_processing_pipeline_from_settings",
        lambda received_settings: captured_settings.append(received_settings) or pipeline,
    )

    worker = automation_module.build_worker_from_env()

    assert captured_settings == [settings]
    assert worker.video_processor is pipeline
    assert worker.creator_profile_provider is not None
    assert worker.creator_profile_provider().identity == "定时 worker 画像"



def test_automation_worker_processes_bound_episode_video_to_draft(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    processed = tmp_path / "processed"
    drafts = tmp_path / "drafts"
    inbox.mkdir()
    (inbox / "raw.mov").write_bytes(b"raw")

    def runner(command: list[str]) -> None:
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"processed")

    jianying_root = tmp_path / "jianying"
    jianying_root.mkdir()
    settings = Settings(
        video_inbox_path=str(inbox),
        video_output_path=str(processed),
        draft_output_path=str(drafts),
        jianying_draft_root_path=str(jianying_root),
    )
    repo = EpisodeRepository(today_provider=lambda: date(2026, 5, 28))
    episode = repo.today()
    episode.status = EpisodeStatus.WAITING_VIDEO
    episode.script = ScriptPackage(
        id="script_1",
        hook="今天聊 AI 视频日记。",
        sections=[ScriptSection("s1", "第一点", 10, "Agent 自动处理视频。")],
        title_options=["标题"],
        cover_copy="AI视频日记 第1天\n自动闭环",
        publish_copy="发布文案",
        comment_prompt="评论引导",
    )
    repo.save(episode)
    processed.mkdir()
    processed_video = processed / "raw_preview.mp4"
    processed_video.write_bytes(b"processed")
    pipeline = CapturingVideoPipeline(
        ProcessedVideo("raw_preview.mp4", str(processed_video), "raw.mov")
    )
    imported_paths: list[Path] = []

    def legacy_writer(package_dir: Path, draft_root: Path, manifest: dict) -> Path:
        draft_path = draft_root / manifest["package_name"]
        draft_path.mkdir()
        (draft_path / "draft_content.json").write_text("{}", encoding="utf-8")
        (draft_path / "draft_info.json").write_text("{}", encoding="utf-8")
        imported_paths.append(draft_path)
        return draft_path

    monkeypatch.setattr(
        DraftWorkspace,
        "draft_tool_status",
        lambda self: DraftToolStatus(True, "pyJianYingDraft", ["pyJianYingDraft"]),
    )

    worker = AgentAutomationWorker(
        repo=repo,
        workflow=LangGraphEpisodeWorkflow(_service()),
        video_inbox=VideoInbox(inbox),
        video_processor=pipeline,
        draft_workspace=DraftWorkspace(
            settings,
            runner=runner,
            legacy_draft_writer=legacy_writer,
        ),
    )

    result = worker.process_pending_videos_once()

    assert result == ["raw.mov"]
    assert pipeline.process_calls == ["raw.mov"]
    updated = repo.today()
    assert updated.video_source_file_name == "raw.mov"
    assert updated.video_processed_file_name == "raw_preview.mp4"
    assert updated.draft_package_name
    manifest = json.loads(
        (drafts / updated.draft_package_name / "manifest.json").read_text(
            encoding="utf-8",
        )
    )
    assert manifest["jianying_import"]["status"] == "imported"
    assert manifest["jianying_import"]["draft_path"] == str(imported_paths[0])
    assert updated.status == EpisodeStatus.DRAFT_READY
