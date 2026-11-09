import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.source_repository import SourceRepository
from app.sources import TopicSourceItem
from app.video import FfmpegVideoProcessor, VideoInbox

client = TestClient(main_module.app)


@pytest.fixture(autouse=True)
def reset_source_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main_module, "source_repository", SourceRepository(tmp_path / "sources.sqlite3")
    )


@pytest.fixture(autouse=True)
def knowledge_notes_metadata_guard(request, monkeypatch, tmp_path):
    if request.node.originalname != 'test_knowledge_endpoint_lists_real_obsidian_notes':
        return None
    import sqlite3
    import subprocess
    import wave
    import httpx
    from types import SimpleNamespace
    from PIL import Image
    from app.auth import SupabaseAuthContext
    from app.device_runtime import DeviceRuntime
    from app.device_scheduler import DeviceScheduler
    from app.sources import ObsidianVault

    violations = []

    def forbidden(*args, **kwargs):
        violations.append('notes-model-network-or-media-execution')
        pytest.fail('notes browsing metadata permits only temporary Markdown and SQLite reads')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    # TestClient has its own in-process transport; block actual network transports.
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('_creation_runtime', '_start_device_runtime', 'run_task_turn', 'run_plan_turn',
                 '_creation_task_topic_generator_for', '_creation_task_script_generator_for',
                 '_execute_script_generation', '_lipsync_provider', 'start_assistant_continuation_driver'):
        monkeypatch.setattr(main_module, name, forbidden)
    settings = Settings(database_path=str(tmp_path / 'studio.sqlite3'),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'output'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main_module, 'settings', settings)
    monkeypatch.setattr(main_module, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main_module, 'device_runtime', None)
    # Records the original value for teardown, including the original test's
    # subsequent direct assignment of its own temporary Vault.
    monkeypatch.setattr(main_module, 'vault', ObsidianVault(None))
    request.getfixturevalue('reset_source_repository')

    def snapshot():
        with sqlite3.connect(f'file:{tmp_path / "sources.sqlite3"}?mode=ro', uri=True) as conn:
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            facts = {table: sorted((tuple(row) for row in conn.execute(
                'SELECT * FROM "' + table.replace('"', '""') + '"')), key=repr) for table in tables}
        return facts, {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                       for path in tmp_path.rglob('*')}

    def check_no_execution():
        assert violations == []

    request.addfinalizer(check_no_execution)
    return SimpleNamespace(snapshot=snapshot, violations=violations)


def test_knowledge_endpoint_lists_real_obsidian_notes(tmp_path, knowledge_notes_metadata_guard):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "agent.md").write_text(
        "# Agent 工作流\n\n人工复核让 Agent 产品更可靠。",
        encoding="utf-8",
    )
    import os
    (vault / "rEfOnLy.md").write_text("# TITLEneedle\n\nstable body", encoding="utf-8")
    (vault / "other.md").write_text("# Neutral\n\nExCerPtneedle", encoding="utf-8")
    for name, stamp in (("agent.md", 1700000300), ("rEfOnLy.md", 1700000200), ("other.md", 1700000100)):
        os.utime(vault / name, (stamp, stamp))
    main_module.vault = main_module.ObsidianVault(vault)

    before_metadata = knowledge_notes_metadata_guard.snapshot()
    response = client.get("/api/knowledge/notes")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["title"] == "Agent 工作流"
    assert body[0]["ref"] == "agent.md"
    assert "人工复核" in body[0]["excerpt"]

    assert [note['ref'] for note in body] == ['agent.md', 'rEfOnLy.md', 'other.md']
    for query, ref in (('titleneedle', 'rEfOnLy.md'), ('REFONLY', 'rEfOnLy.md'), ('excerptneedle', 'other.md')):
        matched = client.get('/api/knowledge/notes', params={'q': query})
        assert matched.status_code == 200 and [note['ref'] for note in matched.json()] == [ref]
    assert client.get('/api/knowledge/notes', params={'q': 'no-matching-phrase'}).json() == []
    clamped = client.get('/api/knowledge/notes', params={'limit': 0})
    assert clamped.status_code == 200 and [note['ref'] for note in clamped.json()] == ['agent.md']
    assert client.get('/api/knowledge/notes', params={'limit': 'invalid'}).status_code == 422
    assert knowledge_notes_metadata_guard.snapshot() == before_metadata
    assert knowledge_notes_metadata_guard.violations == []


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

    sync_response = client.post("/api/sources/sync")
    assert sync_response.status_code == 200

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
                TopicSourceItem(
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










def test_runtime_settings_endpoint_hides_secrets(tmp_path):
    main_module.settings = Settings(
        deepseek_api_key="secret-key",
        asr_provider="tencentcloud",
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
    """文字服务进工作区快照和系统安全存储，其余设备设置仍写 dotenv。"""
    from app import workspace_settings as workspace_settings_module

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
    # 文字服务的模型名归工作区快照，密钥归系统安全存储，两样都不再进 dotenv。
    assert workspace_settings_module.load_workspace_config("local")["deepseek_model"] == "deepseek-chat"
    assert workspace_settings_module.read_workspace_secret("local", "deepseek_api_key") == "new-secret"
    assert main_module.settings.tencentcloud_asr_region == "ap-guangzhou"
    assert main_module.settings.tencentcloud_asr_engine_model_type == "16k_zh"
    assert main_module.video_inbox.path == tmp_path / "inbox"
    assert main_module.video_pipeline is not old_video_pipeline
    assert main_module.video_pipeline.processor.output_path == tmp_path / "out"
    assert main_module.draft_workspace.draft_output_path == tmp_path / "drafts"
    env_text = env_path.read_text(encoding="utf-8")
    assert "DEEPSEEK_MODEL" not in env_text
    assert "DEEPSEEK_API_KEY" not in env_text
    assert "TENCENTCLOUD_ASR_REGION=ap-guangzhou" in env_text
    assert f"VIDEO_INBOX_PATH={tmp_path / 'inbox'}" in env_text
    assert "TOPIC_SOURCE_URLS=https://watcha.cn/,https://example.com/radar" in env_text
