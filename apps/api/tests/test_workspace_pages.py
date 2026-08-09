import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.models import (
    EpisodeStatus,
    FeedbackEvent,
    FeedbackValue,
    ScriptPackage,
    ScriptSection,
)
from app.repository import EpisodeRepository
from app.video import FfmpegVideoProcessor, VideoInbox

client = TestClient(main_module.app)


@pytest.fixture(autouse=True)
def reset_manual_sources():
    main_module.manual_source_items = []


def test_knowledge_endpoint_lists_real_obsidian_notes(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "agent.md").write_text(
        "# Agent 工作流\n\n人工复核让 Agent 产品更可靠。",
        encoding="utf-8",
    )
    main_module.vault = main_module.ObsidianVault(vault)

    response = client.get("/api/knowledge/notes")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["title"] == "Agent 工作流"
    assert body[0]["ref"] == "agent.md"
    assert "人工复核" in body[0]["excerpt"]


def test_sources_endpoint_lists_hermes_radar_items(tmp_path):
    report_dir = tmp_path / "daily-reports"
    report_dir.mkdir()
    (report_dir / "2026-05-27.md").write_text(
        """**Product Hunt 今日热门**（共 1 条）

**1. Runway Agent**
🔗 https://www.producthunt.com/products/runway-agent
📝 Generate edited videos via chat
🇨🇳 通过聊天生成剪辑视频。

---

**观猹 产品库精选**（共 1 条）

**1. VibePPT**
🔗 https://watcha.cn/products/vibeppt
🇨🇳 像聊天一样做 PPT。
""",
        encoding="utf-8",
    )
    main_module.settings = Settings(
        hermes_radar_path=str(report_dir),
        topic_source_urls=(),
    )

    response = client.get("/api/sources/items")

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 2
    assert body["source_counts"] == {"Product Hunt": 1, "观猹": 1}
    assert {item["title"] for item in body["items"]} == {"Runway Agent", "VibePPT"}


def test_sources_sync_endpoint_returns_material_counts(tmp_path):
    report_dir = tmp_path / "daily-reports"
    report_dir.mkdir()
    (report_dir / "2026-05-27.md").write_text(
        """**Product Hunt 今日热门**（共 1 条）

**1. Runway Agent**
🔗 https://www.producthunt.com/products/runway-agent
📝 Generate edited videos via chat
""",
        encoding="utf-8",
    )
    main_module.settings = Settings(
        hermes_radar_path=str(report_dir),
        topic_source_urls=(),
    )

    response = client.post("/api/sources/sync")

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["source_counts"] == {"Product Hunt": 1}


def test_sources_links_endpoint_converts_url_to_material(monkeypatch):
    class FakeExternalClient:
        def __init__(self, urls):
            self.urls = urls

        def fetch_items(self):
            return [
                main_module.TopicSourceItem(
                    source_type="外链",
                    title="AI 产品观察",
                    ref=self.urls[0],
                    excerpt="这是一条手动链接素材。",
                )
            ]

    monkeypatch.setattr(main_module, "HtmlPageSourceClient", FakeExternalClient)

    response = client.post(
        "/api/sources/links",
        json={"url": "https://example.com/ai-product"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["items"][0]["title"] == "AI 产品观察"
    assert body["items"][0]["ref"] == "https://example.com/ai-product"


def test_feedback_summary_counts_topic_and_script_feedback():
    repo = EpisodeRepository(today_provider=lambda: date(2026, 5, 27))
    episode = repo.create()
    episode.feedback_events.append(
        FeedbackEvent("topic", "topic_1", FeedbackValue.UP, "观点好")
    )
    episode.feedback_events.append(
        FeedbackEvent("script_section", "section_1", FeedbackValue.DOWN, "太长")
    )
    repo.save(episode)
    main_module.repo = repo

    response = client.get("/api/feedback/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 2
    assert body["up_count"] == 1
    assert body["down_count"] == 1
    assert body["recent_events"][0]["reason"] == "太长"


def test_preferences_endpoint_turns_feedback_reasons_into_memory_cards():
    repo = EpisodeRepository(today_provider=lambda: date(2026, 5, 27))
    episode = repo.create()
    episode.feedback_events.append(
        FeedbackEvent("topic", "topic_1", FeedbackValue.UP, "多讲真实项目")
    )
    episode.feedback_events.append(
        FeedbackEvent("script_section", "section_1", FeedbackValue.DOWN, "太像概念课")
    )
    episode.feedback_events.append(
        FeedbackEvent("script_section", "section_2", FeedbackValue.UP, "多讲真实项目")
    )
    repo.save(episode)
    main_module.repo = repo

    response = client.get("/api/preferences")

    assert response.status_code == 200
    body = response.json()
    assert body["total_feedback_count"] == 3
    assert body["preferences"][0] == {
        "label": "偏好",
        "signal": "多讲真实项目",
        "evidence_count": 2,
        "confidence": 0.65,
            "enabled": True,
    }
    assert body["preferences"][1]["label"] == "避开"
    assert body["preferences"][1]["signal"] == "太像概念课"


def test_draft_package_endpoint_creates_real_manifest(tmp_path):
    video_output = tmp_path / "processed"
    draft_output = tmp_path / "drafts"
    video_output.mkdir()
    (video_output / "talk_preview.mp4").write_bytes(b"processed")
    main_module.settings = Settings(
        video_output_path=str(video_output),
        draft_output_path=str(draft_output),
        jianying_draft_root_path=str(tmp_path / "jianying"),
    )
    main_module.draft_workspace = main_module.DraftWorkspace(main_module.settings)
    main_module.repo = EpisodeRepository(today_provider=lambda: date(2026, 5, 28))
    episode = main_module.repo.today()
    episode.status = EpisodeStatus.SCRIPT_READY
    episode.script = ScriptPackage(
        id="script_test",
        hook="今天聊拍照识别卡路里的三个问题。",
        sections=[
            ScriptSection(
                id="section_test",
                title="第一点",
                estimated_seconds=20,
                content="识别只是开始，用户真正需要的是持续可用的记录流程。",
            )
        ],
        title_options=["拍照识别卡路里为什么难生存"],
        cover_copy="AI视频日记 第1天\n拍照识别卡路里为什么难生存",
        publish_copy="今天聊一个真实的AI产品难题。",
        comment_prompt="你会用这类工具吗？",
    )
    main_module.repo.save(episode)

    response = client.post(
        "/api/drafts/package",
        json={"video_file_name": "talk_preview.mp4"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["package_name"].startswith("AI视频日记_")
    assert body["subtitle_path"].endswith("subtitle.srt")
    assert body["cover_path"].endswith("cover.png")
    manifest = json.loads((draft_output / body["package_name"] / "manifest.json").read_text())
    assert manifest["video"]["file_name"] == "talk_preview.mp4"
    assert isinstance(manifest["draft_tool"]["available"], bool)
    assert "pyJianYingDraft" in manifest["draft_tool"]["checked_modules"]


def test_draft_import_endpoint_uses_bridge_for_jianying_10_7(tmp_path):
    video_output = tmp_path / "processed"
    draft_output = tmp_path / "drafts"
    jianying_root = tmp_path / "com.lveditor.draft"
    encrypted = jianying_root / "5月28日"
    video_output.mkdir()
    encrypted.mkdir(parents=True)
    (video_output / "talk_preview.mp4").write_bytes(b"processed")
    (encrypted / "draft_info.json").write_text("{}", encoding="utf-8")
    (encrypted / "crypto_key_store.dat").write_bytes(b"crypto")
    main_module.settings = Settings(
        video_output_path=str(video_output),
        draft_output_path=str(draft_output),
        jianying_draft_root_path=str(jianying_root),
    )
    imported_paths: list[Path] = []

    def legacy_writer(package_dir: Path, draft_root: Path, manifest: dict) -> Path:
        draft_path = draft_root / manifest["package_name"]
        draft_path.mkdir()
        (draft_path / "draft_content.json").write_text("{}", encoding="utf-8")
        (draft_path / "draft_info.json").write_text("{}", encoding="utf-8")
        imported_paths.append(draft_path)
        return draft_path

    main_module.draft_workspace = main_module.DraftWorkspace(
        main_module.settings,
        legacy_draft_writer=legacy_writer,
    )
    package = main_module.draft_workspace.create_package("talk_preview.mp4")

    response = client.post(
        "/api/drafts/import",
        json={"package_name": package.package_name},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "imported"
    assert body["draft_path"] == str(imported_paths[0])
    assert "copy_draft_external" in body["message"]

    status_response = client.get("/api/drafts/status")
    package_summary = status_response.json()["packages"][0]
    assert package_summary["import_status"] == "imported"
    assert "copy_draft_external" in package_summary["import_message"]


def test_runtime_settings_endpoint_hides_secrets(tmp_path):
    main_module.settings = Settings(
        deepseek_api_key="secret-key",
        tencentcloud_secret_id="tencent-id",
        tencentcloud_secret_key="tencent-key",
        obsidian_vault_path=str(tmp_path / "vault"),
        hermes_radar_path=str(tmp_path / "daily-reports"),
        database_path=str(tmp_path / "agent.sqlite3"),
    )
    main_module.video_inbox = VideoInbox(main_module.settings.video_inbox_path)
    main_module.video_processor = FfmpegVideoProcessor(main_module.settings)
    main_module.draft_workspace = main_module.DraftWorkspace(main_module.settings)

    response = client.get("/api/settings/runtime")

    assert response.status_code == 200
    dumped = json.dumps(response.json(), ensure_ascii=False)
    assert "secret-key" not in dumped
    assert "tencent-id" not in dumped
    assert "tencent-key" not in dumped
    assert "DEEPSEEK_API_KEY" in dumped
    assert "TENCENTCLOUD_SECRET_ID" in dumped
    assert "DATABASE_PATH" in dumped
    assert response.json()["status"]["asr"]["provider"] == "tencentcloud"
    assert response.json()["status"]["asr"]["configured"] is True
    assert response.json()["status"]["storage"]["database_exists"] is False


def test_workflow_status_exposes_langgraph_nodes():
    response = client.get("/api/workflow/status")

    assert response.status_code == 200
    body = response.json()
    assert body["framework"] == "LangGraph"
    assert "generate_topics" in body["node_names"]
    assert "approve_script" in body["human_review_nodes"]


def test_runtime_settings_endpoint_updates_env_and_runtime(tmp_path):
    env_path = tmp_path / ".env"
    main_module.runtime_env_path = env_path
    main_module.settings = Settings(database_path=str(tmp_path / "old.sqlite3"))
    old_video_pipeline = object()
    main_module.video_pipeline = old_video_pipeline

    response = client.post(
        "/api/settings/runtime",
        json={
            "deepseek_model": "deepseek-chat",
            "deepseek_api_key": "new-secret",
            "tencentcloud_secret_id": "new-tencent-id",
            "tencentcloud_secret_key": "new-tencent-key",
            "tencentcloud_asr_region": "ap-guangzhou",
            "tencentcloud_asr_engine_model_type": "16k_zh",
            "video_inbox_path": str(tmp_path / "inbox"),
            "video_output_path": str(tmp_path / "out"),
            "draft_output_path": str(tmp_path / "drafts"),
            "database_path": str(tmp_path / "new.sqlite3"),
            "topic_source_urls": ["https://watcha.cn/", "https://example.com/radar"],
        },
    )

    assert response.status_code == 200
    dumped = json.dumps(response.json(), ensure_ascii=False)
    assert "new-secret" not in dumped
    assert "new-tencent-id" not in dumped
    assert "new-tencent-key" not in dumped
    assert main_module.settings.deepseek_model == "deepseek-chat"
    assert main_module.settings.tencentcloud_asr_region == "ap-guangzhou"
    assert main_module.settings.tencentcloud_asr_engine_model_type == "16k_zh"
    assert main_module.video_inbox.path == tmp_path / "inbox"
    assert main_module.video_pipeline is not old_video_pipeline
    assert main_module.video_pipeline.processor.output_path == tmp_path / "out"
    assert main_module.draft_workspace.draft_output_path == tmp_path / "drafts"
    env_text = env_path.read_text(encoding="utf-8")
    assert "DEEPSEEK_MODEL=deepseek-chat" in env_text
    assert "TENCENTCLOUD_ASR_REGION=ap-guangzhou" in env_text
    assert f"VIDEO_INBOX_PATH={tmp_path / 'inbox'}" in env_text
    assert "TOPIC_SOURCE_URLS=https://watcha.cn/,https://example.com/radar" in env_text
