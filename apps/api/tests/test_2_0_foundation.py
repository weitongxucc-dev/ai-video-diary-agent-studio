from datetime import date

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.repository import EpisodeRepository

client = TestClient(main_module.app)


def test_content_templates_endpoint_returns_three_product_templates():
    response = client.get("/api/content-templates")

    assert response.status_code == 200
    body = response.json()
    template_ids = {template["template_id"] for template in body["templates"]}
    assert {"video_diary", "tool_review", "tutorial_demo"}.issubset(template_ids)
    for template in body["templates"]:
        assert template["name"]
        assert template["recommended_duration"]
        assert template["topic_structure"]
        assert template["script_structure"]
        assert template["material_requirements"]
        assert template["jianying_style"]
        assert template["default_feedback_dimensions"]


def test_episode_content_template_can_be_selected_and_persisted(tmp_path):
    database_path = tmp_path / "agent.sqlite3"
    main_module.repo = EpisodeRepository(
        today_provider=lambda: date(2026, 6, 7),
        database_path=database_path,
    )
    episode = main_module.repo.today()

    response = client.post(
        f"/api/episodes/{episode.id}/content-template",
        json={"template_id": "tool_review"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content_template_id"] == "tool_review"

    reloaded_repo = EpisodeRepository(
        today_provider=lambda: date(2026, 6, 7),
        database_path=database_path,
    )
    assert reloaded_repo.today().content_template_id == "tool_review"


def test_runtime_health_endpoint_explains_status_and_repair_actions(tmp_path):
    video_inbox = tmp_path / "inbox"
    video_output = tmp_path / "output"
    video_inbox.mkdir()
    video_output.mkdir()
    main_module.settings = Settings(
        deepseek_api_key=None,
        obsidian_vault_path=str(tmp_path / "missing-vault"),
        video_inbox_path=str(video_inbox),
        video_output_path=str(video_output),
        draft_output_path=str(tmp_path / "drafts"),
        jianying_draft_root_path=str(tmp_path / "jianying"),
        database_path=str(tmp_path / "agent.sqlite3"),
        topic_source_urls=("https://watcha.cn/",),
    )

    response = client.get("/api/runtime/health")

    assert response.status_code == 200
    body = response.json()
    items = {item["id"]: item for item in body["items"]}
    assert body["summary"]["total"] == len(body["items"])
    assert items["deepseek"]["status"] == "missing"
    assert items["deepseek"]["impact"]
    assert items["deepseek"]["repair_action"]
    assert items["obsidian"]["status"] == "missing"
    assert items["video_inbox"]["status"] == "ready"
    assert items["video_output"]["status"] == "ready"
    assert "last_checked_at" in items["deepseek"]
