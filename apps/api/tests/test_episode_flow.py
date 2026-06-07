import pytest
from fastapi.testclient import TestClient
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace

import app.main as main_module
from app.llm import TemplateScriptGenerator
from app.models import FeedbackEvent, FeedbackValue
from app.repository import EpisodeRepository
from app.schemas import ProcessedVideoResponse
from app.services import EpisodeService
from app.topics import TemplateTopicGenerator
from app.video import ProcessedVideo


app = main_module.app
client = TestClient(app)


class InlineVideoJobRunner:
    def is_active(self, episode_id: str) -> bool:
        return False

    def submit(self, episode_id: str, job) -> bool:
        job()
        return True


class CapturingVideoJobRunner:
    def __init__(self) -> None:
        self.active_episode_ids = set()
        self.jobs = []

    def is_active(self, episode_id: str) -> bool:
        return episode_id in self.active_episode_ids

    def submit(self, episode_id: str, job) -> bool:
        if self.is_active(episode_id):
            return False
        self.active_episode_ids.add(episode_id)
        self.jobs.append(job)
        return True


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


class PassthroughVideoPipeline:
    def __init__(self, processor) -> None:
        self.processor = processor

    def process(self, file_name: str) -> ProcessedVideo:
        return self.processor.process(file_name)

    def process_batch(self, file_names: list[str]) -> ProcessedVideo:
        return self.processor.process_batch(file_names)


class StubDraftWorkspace:
    def create_package(self, video_file_name: str, episode):
        return SimpleNamespace(package_name="draft_package", path="/tmp/draft_package")

    def create_package_and_import_to_jianying(self, video_file_name: str, episode):
        return self.create_package(video_file_name, episode)


@pytest.fixture(autouse=True)
def reset_repository():
    main_module.repo = EpisodeRepository()
    main_module.service = EpisodeService(script_generator=TemplateScriptGenerator())
    main_module.workflow = main_module.LangGraphEpisodeWorkflow(main_module.service)
    main_module.video_job_runner = InlineVideoJobRunner()
    main_module.video_pipeline = PassthroughVideoPipeline(main_module.video_processor)


def test_standalone_video_processing_uses_pipeline():
    pipeline = CapturingVideoPipeline(
        ProcessedVideo("raw_preview.mp4", "/tmp/raw_preview.mp4", "raw.mov")
    )
    main_module.video_pipeline = pipeline

    response = client.post("/api/videos/process", json={"file_name": "raw.mov"})

    assert response.status_code == 200
    assert pipeline.process_calls == ["raw.mov"]


def test_planned_episode_video_processing_uses_pipeline():
    pipeline = CapturingVideoPipeline(
        ProcessedVideo("raw_preview.mp4", "/tmp/raw_preview.mp4", "raw.mov")
    )
    main_module.video_pipeline = pipeline
    episode = main_module.repo.create()

    response = client.post(
        f"/api/episodes/{episode.id}/process-video",
        json={"file_name": "raw.mov"},
    )

    assert response.status_code == 200
    assert pipeline.process_calls == ["raw.mov"]


def test_ad_hoc_video_background_completion_uses_pipeline():
    pipeline = CapturingVideoPipeline(
        ProcessedVideo("raw_preview.mp4", "/tmp/raw_preview.mp4", "raw.mov")
    )
    main_module.video_pipeline = pipeline
    main_module.draft_workspace = StubDraftWorkspace()
    episode = main_module.repo.create()

    main_module._complete_ad_hoc_episode_video(episode.id, "raw.mov")

    assert pipeline.process_calls == ["raw.mov"]
    assert main_module.repo.get(episode.id).status == main_module.EpisodeStatus.DRAFT_READY


def test_ad_hoc_video_batch_background_completion_uses_pipeline():
    pipeline = CapturingVideoPipeline(
        ProcessedVideo(
            "part-02_batch_2_preview.mp4",
            "/tmp/part-02_batch_2_preview.mp4",
            "part-02.mov",
            source_file_names=["part-02.mov", "part-01.mov"],
        )
    )
    main_module.video_pipeline = pipeline
    main_module.draft_workspace = StubDraftWorkspace()
    episode = main_module.repo.create()

    main_module._complete_ad_hoc_episode_video_batch(
        episode.id,
        ["part-02.mov", "part-01.mov"],
    )

    assert pipeline.process_batch_calls == [["part-02.mov", "part-01.mov"]]
    assert main_module.repo.get(episode.id).status == main_module.EpisodeStatus.DRAFT_READY


def test_generate_topics_for_missing_episode_returns_404():
    client_without_raises = TestClient(app, raise_server_exceptions=False)

    response = client_without_raises.post("/api/episodes/missing/generate-topics")

    assert response.status_code == 404


def test_generate_topics_receives_cross_episode_feedback_history():
    class CapturingTopicGenerator:
        def __init__(self) -> None:
            self.feedback_events = []

        def generate(self, day_number, feedback_events=None, template=None):
            self.feedback_events = feedback_events or []
            return TemplateTopicGenerator().generate(
                day_number,
                feedback_events,
                template=template,
            )

    repo = EpisodeRepository(today_provider=lambda: date(2026, 5, 28))
    old_episode = repo.create(date(2026, 5, 27))
    old_episode.feedback_events.append(
        FeedbackEvent("topic", "old_topic", FeedbackValue.DOWN, "太泛，少讲空概念")
    )
    repo.save(old_episode)
    today = repo.today()
    generator = CapturingTopicGenerator()
    main_module.repo = repo
    main_module.service = EpisodeService(
        script_generator=TemplateScriptGenerator(),
        topic_generator=generator,
    )
    main_module.workflow = main_module.LangGraphEpisodeWorkflow(main_module.service)

    response = client.post(f"/api/episodes/{today.id}/generate-topics")

    assert response.status_code == 200
    assert [event.reason for event in generator.feedback_events] == ["太泛，少讲空概念"]


def test_today_episode_flow_generates_topics_and_script():
    today = client.get("/api/episodes/today").json()
    assert today["status"] == "created"

    topics_response = client.post(f"/api/episodes/{today['id']}/generate-topics")
    assert topics_response.status_code == 200
    topics = topics_response.json()["topics"]
    assert len(topics) == 4
    assert topics[0]["source_refs"]

    approved = client.post(
        f"/api/episodes/{today['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    ).json()
    assert approved["status"] == "topic_approved"
    assert approved["selected_topic_id"] == topics[0]["id"]

    script = client.post(f"/api/episodes/{today['id']}/generate-script").json()["script"]
    assert script["hook"]
    assert len(script["sections"]) >= 5


def test_feedback_updates_topic_feedback_value():
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]

    feedback_response = client.post(
        f"/api/episodes/{episode['id']}/feedback",
        json={
            "target_type": "topic",
            "target_id": topics[0]["id"],
            "value": "down",
            "reason": "太泛",
        },
    )

    assert feedback_response.status_code == 200
    body = feedback_response.json()
    updated_topic = body["topics"][0]
    assert updated_topic["feedback"] == "down"
    assert body["feedback_events"][-1]["target_type"] == "topic"
    assert body["feedback_events"][-1]["target_id"] == topics[0]["id"]
    assert body["feedback_events"][-1]["value"] == "down"
    assert body["feedback_events"][-1]["reason"] == "太泛"


def test_feedback_event_response_target_type_is_documented_as_enum():
    openapi = client.get("/openapi.json").json()
    target_type_schema = openapi["components"]["schemas"]["FeedbackEventResponse"][
        "properties"
    ]["target_type"]

    assert target_type_schema["enum"] == ["topic", "script_section"]


def test_feedback_can_be_reverted_and_is_excluded_from_future_generation():
    class CapturingTopicGenerator:
        def __init__(self) -> None:
            self.feedback_events = []

        def generate(self, day_number, feedback_events=None, template=None):
            self.feedback_events = feedback_events or []
            return TemplateTopicGenerator().generate(
                day_number,
                feedback_events,
                template=template,
            )

    generator = CapturingTopicGenerator()
    main_module.service = EpisodeService(
        script_generator=TemplateScriptGenerator(),
        topic_generator=generator,
    )
    main_module.workflow = main_module.LangGraphEpisodeWorkflow(main_module.service)
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    feedback = client.post(
        f"/api/episodes/{episode['id']}/feedback",
        json={
            "target_type": "topic",
            "target_id": topics[0]["id"],
            "value": "down",
            "reason": "这个方向太泛",
        },
    ).json()["feedback_events"][-1]

    reverted = client.post(
        f"/api/episodes/{episode['id']}/feedback/revert",
        json={"feedback_event_id": feedback["id"], "reason": "误点"},
    )

    assert reverted.status_code == 200
    body = reverted.json()
    assert body["feedback_events"][-1]["active"] is False
    assert body["feedback_events"][-1]["revert_reason"] == "误点"

    client.post(f"/api/episodes/{episode['id']}/regenerate-topics")
    assert [event.reason for event in generator.feedback_events] == []


def test_feedback_summary_shows_reverted_events_without_counting_them():
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    feedback = client.post(
        f"/api/episodes/{episode['id']}/feedback",
        json={
            "target_type": "topic",
            "target_id": topics[0]["id"],
            "value": "up",
            "reason": "这个方向可以",
        },
    ).json()["feedback_events"][-1]
    client.post(
        f"/api/episodes/{episode['id']}/feedback/revert",
        json={"feedback_event_id": feedback["id"], "reason": "撤回样本"},
    )

    summary = client.get("/api/feedback/summary").json()

    assert summary["total_count"] == 0
    assert summary["up_count"] == 0
    assert summary["recent_events"][0]["id"] == feedback["id"]
    assert summary["recent_events"][0]["episode_id"] == episode["id"]
    assert summary["recent_events"][0]["active"] is False
    assert summary["recent_events"][0]["revert_reason"] == "撤回样本"


def test_regenerating_topics_preserves_selected_topic_and_script():
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    )
    script = client.post(f"/api/episodes/{episode['id']}/generate-script").json()["script"]

    response = client.post(f"/api/episodes/{episode['id']}/generate-topics")

    assert response.status_code == 200
    updated_episode = response.json()
    assert [topic["id"] for topic in updated_episode["topics"]] == [
        topic["id"] for topic in topics
    ]
    assert updated_episode["selected_topic_id"] == topics[0]["id"]
    assert updated_episode["script"]["id"] == script["id"]
    assert updated_episode["status"] == "script_ready"
    assert len(updated_episode["topics"]) == 4


def test_force_regenerating_topics_replaces_candidates_and_clears_script():
    episode = client.post("/api/episodes").json()
    first_topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": first_topics[0]["id"]},
    )
    client.post(f"/api/episodes/{episode['id']}/generate-script")

    response = client.post(f"/api/episodes/{episode['id']}/regenerate-topics")

    assert response.status_code == 200
    updated_episode = response.json()
    assert [topic["id"] for topic in updated_episode["topics"]] != [
        topic["id"] for topic in first_topics
    ]
    assert updated_episode["selected_topic_id"] is None
    assert updated_episode["script"] is None
    assert updated_episode["status"] == "topics_ready"
    assert len(updated_episode["topics"]) == 4


def test_generating_script_twice_preserves_script():
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    )
    script = client.post(f"/api/episodes/{episode['id']}/generate-script").json()["script"]

    response = client.post(f"/api/episodes/{episode['id']}/generate-script")

    assert response.status_code == 200
    updated_episode = response.json()
    assert updated_episode["script"]["id"] == script["id"]
    assert updated_episode["status"] == "script_ready"


def test_script_can_be_edited_saved_and_reverted():
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    )
    generated = client.post(f"/api/episodes/{episode['id']}/generate-script").json()
    original_hook = generated["script"]["hook"]

    edited_payload = {
        "reason": "改成我的口吻",
        "script": {
            **generated["script"],
            "hook": "这是我手动改过的开头",
            "sections": [
                {**section, "content": "这是我手动改过的段落"}
                for section in generated["script"]["sections"]
            ],
        },
    }
    edited = client.post(
        f"/api/episodes/{episode['id']}/script",
        json=edited_payload,
    )

    assert edited.status_code == 200
    edited_body = edited.json()
    assert edited_body["script"]["hook"] == "这是我手动改过的开头"
    assert edited_body["script_versions"][-1]["source"] == "manual_edit"
    assert edited_body["script_versions"][-1]["reason"] == "改成我的口吻"

    original_version_id = edited_body["script_versions"][0]["id"]
    reverted = client.post(
        f"/api/episodes/{episode['id']}/script/revert",
        json={"version_id": original_version_id, "reason": "回到原始稿"},
    )

    assert reverted.status_code == 200
    reverted_body = reverted.json()
    assert reverted_body["script"]["hook"] == original_hook
    assert reverted_body["script_versions"][-1]["source"] == "revert"
    assert reverted_body["script_versions"][-1]["reason"] == "回到原始稿"


def test_script_full_regeneration_creates_new_version():
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    )
    generated = client.post(f"/api/episodes/{episode['id']}/generate-script").json()

    response = client.post(
        f"/api/episodes/{episode['id']}/script/regenerate",
        json={"reason": "需要更犀利一点"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["script"]["id"] != generated["script"]["id"]
    assert body["script_versions"][-1]["source"] == "full_regeneration"
    assert body["script_versions"][-1]["reason"] == "需要更犀利一点"


def test_script_section_regeneration_replaces_only_target_section():
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    )
    generated = client.post(f"/api/episodes/{episode['id']}/generate-script").json()
    section_id = generated["script"]["sections"][0]["id"]
    untouched = generated["script"]["sections"][1]["content"]

    response = client.post(
        f"/api/episodes/{episode['id']}/script/sections/{section_id}/regenerate",
        json={"reason": "这一段要更像日记"},
    )

    assert response.status_code == 200
    body = response.json()
    assert (
        body["script"]["sections"][0]["content"]
        != generated["script"]["sections"][0]["content"]
    )
    assert body["script"]["sections"][1]["content"] == untouched
    assert body["script_versions"][-1]["source"] == "section_regeneration"
    assert body["script_versions"][-1]["changed_section_ids"] == [section_id]


def test_episode_video_to_draft_to_publish_flow_updates_episode_state(tmp_path):
    inbox = tmp_path / "inbox"
    processed = tmp_path / "processed"
    drafts = tmp_path / "drafts"
    inbox.mkdir()
    (inbox / "raw.mov").write_bytes(b"raw video")
    calls = []

    def runner(command: list[str]) -> None:
        calls.append(command)
        output_path = command[-1]
        if isinstance(output_path, str):
            from pathlib import Path

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"processed video")

    main_module.video_inbox = main_module.VideoInbox(inbox)
    main_module.video_processor = main_module.FfmpegVideoProcessor(
        main_module.Settings(
            video_inbox_path=str(inbox),
            video_output_path=str(processed),
            draft_output_path=str(drafts),
            jianying_draft_root_path=str(tmp_path / "jianying"),
        ),
        runner=runner,
    )
    main_module.video_pipeline = PassthroughVideoPipeline(main_module.video_processor)
    main_module.draft_workspace = main_module.DraftWorkspace(
        main_module.Settings(
            video_inbox_path=str(inbox),
            video_output_path=str(processed),
            draft_output_path=str(drafts),
            jianying_draft_root_path=str(tmp_path / "jianying"),
        ),
        runner=runner,
    )

    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    )
    client.post(f"/api/episodes/{episode['id']}/generate-script")

    approved = client.post(f"/api/episodes/{episode['id']}/approve-script").json()
    assert approved["status"] == "waiting_video"

    uploaded = client.post(
        f"/api/episodes/{episode['id']}/upload-video",
        json={"file_name": "raw.mov"},
    ).json()
    assert uploaded["video_source_file_name"] == "raw.mov"
    assert uploaded["status"] == "waiting_video"

    processed_response = client.post(
        f"/api/episodes/{episode['id']}/process-video",
        json={},
    ).json()
    assert processed_response["status"] == "video_processing"
    assert processed_response["video_processed_file_name"] == "raw_preview.mp4"
    assert processed_response["video_edit_plan_path"].endswith("raw_preview.edit_plan.json")

    drafted = client.post(f"/api/episodes/{episode['id']}/create-draft").json()
    assert drafted["status"] == "draft_ready"
    assert drafted["draft_package_name"].startswith("AI视频日记_")
    assert drafted["draft_package_path"]

    published = client.post(
        f"/api/episodes/{episode['id']}/mark-published",
        json={"publish_url": "https://www.douyin.com/video/1", "play_count": 128},
    ).json()
    assert published["status"] == "published"
    assert published["publish_url"] == "https://www.douyin.com/video/1"
    assert published["play_count"] == 128
    assert published["published_at"]


def test_ad_hoc_video_can_skip_topic_and_script_and_create_draft(tmp_path):
    inbox = tmp_path / "inbox"
    processed = tmp_path / "processed"
    drafts = tmp_path / "drafts"
    inbox.mkdir()
    (inbox / "raw.mov").write_bytes(b"already recorded video")

    def runner(command: list[str]) -> None:
        output_path = command[-1]
        if isinstance(output_path, str):
            from pathlib import Path

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"processed video")

    settings = main_module.Settings(
        video_inbox_path=str(inbox),
        video_output_path=str(processed),
        draft_output_path=str(drafts),
        jianying_draft_root_path=str(tmp_path / "jianying"),
    )
    main_module.video_inbox = main_module.VideoInbox(inbox)
    main_module.video_processor = main_module.FfmpegVideoProcessor(
        settings,
        runner=runner,
    )
    main_module.video_pipeline = PassthroughVideoPipeline(main_module.video_processor)
    main_module.draft_workspace = main_module.DraftWorkspace(
        settings,
        runner=runner,
    )
    episode = client.post("/api/episodes").json()

    response = client.post(
        f"/api/episodes/{episode['id']}/process-ad-hoc-video",
        json={"file_name": "raw.mov"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["intake_mode"] == "ad_hoc_video"
    assert body["selected_topic_id"] is None
    assert body["script"] is None
    assert body["status"] == "draft_ready"
    assert body["video_source_file_name"] == "raw.mov"
    assert body["video_processed_file_name"] == "raw_preview.mp4"
    assert body["draft_package_path"]
    manifest = json.loads(
        (Path(body["draft_package_path"]) / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["source_mode"] == "ad_hoc_video"
    assert manifest["artifacts"]["subtitle_source"] == "none"
    assert manifest["artifacts"]["cover_path"]


def test_ad_hoc_video_clears_planned_topic_and_script_context(tmp_path):
    inbox = tmp_path / "inbox"
    processed = tmp_path / "processed"
    drafts = tmp_path / "drafts"
    inbox.mkdir()
    (inbox / "raw.mov").write_bytes(b"already recorded video")

    def runner(command: list[str]) -> None:
        output_path = command[-1]
        if isinstance(output_path, str):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"processed video")

    settings = main_module.Settings(
        video_inbox_path=str(inbox),
        video_output_path=str(processed),
        draft_output_path=str(drafts),
        jianying_draft_root_path=str(tmp_path / "jianying"),
    )
    main_module.video_inbox = main_module.VideoInbox(inbox)
    main_module.video_processor = main_module.FfmpegVideoProcessor(settings, runner=runner)
    main_module.video_pipeline = PassthroughVideoPipeline(main_module.video_processor)
    main_module.draft_workspace = main_module.DraftWorkspace(settings, runner=runner)
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    )
    client.post(f"/api/episodes/{episode['id']}/generate-script")

    response = client.post(
        f"/api/episodes/{episode['id']}/process-ad-hoc-video",
        json={"file_name": "raw.mov"},
    )

    body = response.json()
    assert body["intake_mode"] == "ad_hoc_video"
    assert body["topics"] == []
    assert body["selected_topic_id"] is None
    assert body["script"] is None


def test_ad_hoc_video_batch_merges_ordered_clips_and_creates_draft(tmp_path):
    inbox = tmp_path / "inbox"
    processed = tmp_path / "processed"
    drafts = tmp_path / "drafts"
    inbox.mkdir()
    (inbox / "part-02.mov").write_bytes(b"second")
    (inbox / "part-01.mov").write_bytes(b"first")
    calls = []

    def runner(command: list[str]) -> None:
        calls.append(command)
        output_path = command[-1]
        if isinstance(output_path, str):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"processed video")

    settings = main_module.Settings(
        video_inbox_path=str(inbox),
        video_output_path=str(processed),
        draft_output_path=str(drafts),
        jianying_draft_root_path=str(tmp_path / "jianying"),
    )
    main_module.video_inbox = main_module.VideoInbox(inbox)
    main_module.video_processor = main_module.FfmpegVideoProcessor(
        settings,
        runner=runner,
        silence_detector=lambda path: [],
    )
    main_module.video_pipeline = PassthroughVideoPipeline(main_module.video_processor)
    main_module.draft_workspace = main_module.DraftWorkspace(settings, runner=runner)
    episode = client.post("/api/episodes").json()

    response = client.post(
        f"/api/episodes/{episode['id']}/process-ad-hoc-video-batch",
        json={"file_names": ["part-02.mov", "part-01.mov"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["intake_mode"] == "ad_hoc_video_batch"
    assert body["video_source_file_names"] == ["part-02.mov", "part-01.mov"]
    assert body["status"] == "draft_ready"
    assert "concat=n=2:v=1:a=1" in " ".join(calls[0])
    manifest = json.loads(
        (Path(body["draft_package_path"]) / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["episode"]["video_source_file_names"] == [
        "part-02.mov",
        "part-01.mov",
    ]


def test_ad_hoc_video_returns_processing_immediately_and_ignores_duplicate_submission(
    tmp_path,
):
    inbox = tmp_path / "inbox"
    processed = tmp_path / "processed"
    drafts = tmp_path / "drafts"
    inbox.mkdir()
    (inbox / "raw.mov").write_bytes(b"already recorded video")

    settings = main_module.Settings(
        video_inbox_path=str(inbox),
        video_output_path=str(processed),
        draft_output_path=str(drafts),
        jianying_draft_root_path=str(tmp_path / "jianying"),
    )
    main_module.video_inbox = main_module.VideoInbox(inbox)
    main_module.video_processor = main_module.FfmpegVideoProcessor(
        settings,
        runner=lambda command: None,
    )
    main_module.video_pipeline = PassthroughVideoPipeline(main_module.video_processor)
    main_module.draft_workspace = main_module.DraftWorkspace(
        settings,
        runner=lambda command: None,
    )
    main_module.video_job_runner = CapturingVideoJobRunner()
    episode = client.post("/api/episodes").json()

    first_response = client.post(
        f"/api/episodes/{episode['id']}/process-ad-hoc-video",
        json={"file_name": "raw.mov"},
    )
    second_response = client.post(
        f"/api/episodes/{episode['id']}/process-ad-hoc-video",
        json={"file_name": "raw.mov"},
    )

    assert first_response.status_code == 200
    assert first_response.json()["status"] == "video_processing"
    assert first_response.json()["video_job_status"] == "processing"
    assert first_response.json()["video_job_message"] == "后台正在处理视频"
    assert first_response.json()["video_job_error"] is None
    assert first_response.json()["video_job_started_at"]
    assert first_response.json()["video_job_finished_at"] is None
    assert second_response.status_code == 200
    assert second_response.json()["status"] == "video_processing"
    assert second_response.json()["video_job_status"] == "processing"
    assert len(main_module.video_job_runner.jobs) == 1


def test_ad_hoc_video_failure_records_actionable_job_error():
    class FailingVideoPipeline:
        def process(self, file_name: str) -> ProcessedVideo:
            raise RuntimeError("ffmpeg exited with code 1")

        def process_batch(self, file_names: list[str]) -> ProcessedVideo:
            raise AssertionError("batch should not be called")

    main_module.video_pipeline = FailingVideoPipeline()
    episode = main_module.repo.create()

    main_module._complete_ad_hoc_episode_video(episode.id, "raw.mov")

    failed = client.get(f"/api/episodes/{episode.id}").json()
    assert failed["status"] == "failed"
    assert failed["video_job_status"] == "failed"
    assert failed["video_job_message"] == "视频处理失败"
    assert "RuntimeError" in failed["video_job_error"]
    assert "ffmpeg exited with code 1" in failed["video_job_error"]
    assert failed["video_job_finished_at"]


def test_interrupted_video_processing_is_recovered_on_episode_read():
    episode = main_module.repo.create()
    episode.status = main_module.EpisodeStatus.VIDEO_PROCESSING
    episode.video_job_status = "processing"
    episode.video_job_message = "后台正在处理视频"
    main_module.repo.save(episode)
    main_module.video_job_runner = CapturingVideoJobRunner()

    response = client.get(f"/api/episodes/{episode.id}")

    body = response.json()
    assert body["status"] == "failed"
    assert body["video_job_status"] == "interrupted"
    assert body["video_job_message"] == "后台任务已中断"
    assert "API restarted" in body["video_job_error"]
    assert body["video_job_finished_at"]


def test_episode_upload_video_rejects_missing_pending_video(tmp_path):
    main_module.video_inbox = main_module.VideoInbox(tmp_path / "inbox")
    episode = client.post("/api/episodes").json()

    response = client.post(
        f"/api/episodes/{episode['id']}/upload-video",
        json={"file_name": "missing.mov"},
    )

    assert response.status_code == 400


def test_episode_exposes_bound_retake_report(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(video_output_path=str(tmp_path)))
    report_path = tmp_path / "raw_preview_dedup_report.json"
    report_path.write_text(
        json.dumps(
            {
                "status": "applied",
                "source_video": "/tmp/raw_preview.mp4",
                "output_video": "/tmp/raw_preview_dedup.mp4",
                "auto_removed": [
                    {
                        "remove": {"start": 13.8, "end": 24.6},
                        "keep": {"start": 24.6, "end": 31.6},
                        "confidence": 1.0,
                        "reason": "repeated_take",
                    }
                ],
                "review_only": [],
                "message": "High-confidence repeated takes were removed",
            }
        ),
        encoding="utf-8",
    )
    episode = main_module.repo.create()
    processed = ProcessedVideo(
        file_name="raw_preview_dedup.mp4",
        output_path="/tmp/raw_preview_dedup.mp4",
        source_file_name="raw.mov",
        dedup_report_path=str(report_path),
    )

    bound = main_module.workflow.run(
        "bind_processed_video",
        episode,
        video=processed,
    )
    main_module.repo.save(bound)

    processed_response = ProcessedVideoResponse.model_validate(
        processed,
        from_attributes=True,
    )
    episode_response = client.get("/api/episodes/today")
    report_response = client.get(f"/api/episodes/{episode.id}/retake-report")

    assert processed_response.dedup_report_path == str(report_path)
    assert episode_response.status_code == 200
    assert episode_response.json()["video_dedup_report_path"] == str(report_path)
    assert report_response.status_code == 200
    assert report_response.json()["status"] == "applied"
    assert report_response.json()["auto_removed"] == [
        {
            "remove": {"start": 13.8, "end": 24.6},
            "keep": {"start": 24.6, "end": 31.6},
            "confidence": 1.0,
            "reason": "repeated_take",
        }
    ]


def test_episode_retake_report_returns_404_without_report_path():
    episode = main_module.repo.create()

    response = client.get(f"/api/episodes/{episode.id}/retake-report")

    assert response.status_code == 404


def test_episode_retake_report_returns_404_when_report_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(video_output_path=str(tmp_path)))
    episode = main_module.repo.create()
    processed = ProcessedVideo(
        file_name="raw_preview_dedup.mp4",
        output_path="/tmp/raw_preview_dedup.mp4",
        source_file_name="raw.mov",
        dedup_report_path=str(tmp_path / "missing.json"),
    )
    bound = main_module.workflow.run(
        "bind_processed_video",
        episode,
        video=processed,
    )
    main_module.repo.save(bound)

    response = client.get(f"/api/episodes/{episode.id}/retake-report")

    assert response.status_code == 404


def test_episode_retake_report_returns_404_outside_video_output_path(tmp_path, monkeypatch):
    output_path = tmp_path / "processed"
    report_path = tmp_path / "outside.json"
    report_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(video_output_path=str(output_path)))
    episode = main_module.repo.create()
    processed = ProcessedVideo(
        file_name="raw_preview_dedup.mp4",
        output_path="/tmp/raw_preview_dedup.mp4",
        source_file_name="raw.mov",
        dedup_report_path=str(report_path),
    )
    bound = main_module.workflow.run(
        "bind_processed_video",
        episode,
        video=processed,
    )
    main_module.repo.save(bound)

    response = client.get(f"/api/episodes/{episode.id}/retake-report")

    assert response.status_code == 404


def test_episode_retake_report_returns_404_for_invalid_json(tmp_path, monkeypatch):
    report_path = tmp_path / "raw_preview_dedup_report.json"
    report_path.write_text("{", encoding="utf-8")
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(video_output_path=str(tmp_path)))
    episode = main_module.repo.create()
    processed = ProcessedVideo(
        file_name="raw_preview_dedup.mp4",
        output_path="/tmp/raw_preview_dedup.mp4",
        source_file_name="raw.mov",
        dedup_report_path=str(report_path),
    )
    bound = main_module.workflow.run(
        "bind_processed_video",
        episode,
        video=processed,
    )
    main_module.repo.save(bound)

    response = client.get(f"/api/episodes/{episode.id}/retake-report")

    assert response.status_code == 404


def test_latest_retake_report_returns_previous_valid_report(tmp_path, monkeypatch):
    report_path = tmp_path / "raw_preview_dedup_report.json"
    report_path.write_text(
        json.dumps(
            {
                "status": "applied",
                "source_video": "/tmp/raw_preview.mp4",
                "output_video": "/tmp/raw_preview_dedup.mp4",
                "auto_removed": [],
                "review_only": [],
                "message": "High-confidence repeated takes were removed",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(video_output_path=str(tmp_path)))
    previous_episode = main_module.repo.create(date(2026, 6, 1))
    processed = ProcessedVideo(
        file_name="raw_preview_dedup.mp4",
        output_path="/tmp/raw_preview_dedup.mp4",
        source_file_name="raw.mov",
        dedup_report_path=str(report_path),
    )
    bound = main_module.workflow.run(
        "bind_processed_video",
        previous_episode,
        video=processed,
    )
    main_module.repo.save(bound)
    main_module.repo.create(date(2026, 6, 2))

    response = client.get("/api/retake-reports/latest")

    assert response.status_code == 200
    assert response.json()["output_video"] == "/tmp/raw_preview_dedup.mp4"


def test_latest_retake_report_uses_most_recent_binding(tmp_path, monkeypatch):
    older_report_path = tmp_path / "older_dedup_report.json"
    newer_report_path = tmp_path / "newer_dedup_report.json"
    for report_path, output_video in (
        (older_report_path, "/tmp/older_dedup.mp4"),
        (newer_report_path, "/tmp/newer_dedup.mp4"),
    ):
        report_path.write_text(
            json.dumps(
                {
                    "status": "applied",
                    "source_video": "/tmp/raw_preview.mp4",
                    "output_video": output_video,
                    "auto_removed": [],
                    "review_only": [],
                    "message": "High-confidence repeated takes were removed",
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(video_output_path=str(tmp_path)))
    older_episode = main_module.repo.create(date(2026, 6, 1))
    newer_episode = main_module.repo.create(date(2026, 6, 2))
    for episode, report_path in (
        (older_episode, older_report_path),
        (newer_episode, newer_report_path),
        (older_episode, older_report_path),
    ):
        processed = ProcessedVideo(
            file_name="raw_preview_dedup.mp4",
            output_path="/tmp/raw_preview_dedup.mp4",
            source_file_name="raw.mov",
            dedup_report_path=str(report_path),
        )
        bound = main_module.workflow.run(
            "bind_processed_video",
            episode,
            video=processed,
        )
        main_module.repo.save(bound)

    response = client.get("/api/retake-reports/latest")

    assert response.status_code == 200
    assert response.json()["output_video"] == "/tmp/older_dedup.mp4"


def test_feedback_for_missing_topic_returns_404():
    episode = client.post("/api/episodes").json()

    response = client.post(
        f"/api/episodes/{episode['id']}/feedback",
        json={
            "target_type": "topic",
            "target_id": "missing",
            "value": "down",
            "reason": "不存在",
        },
    )

    assert response.status_code == 404
