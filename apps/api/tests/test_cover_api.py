from fastapi.testclient import TestClient

import app.main as main_module
from app.llm import TemplateScriptGenerator
from app.repository import EpisodeRepository
from app.services import EpisodeService


client = TestClient(main_module.app)


def reset_cover_runtime():
    main_module.repo = EpisodeRepository()
    main_module.service = EpisodeService(script_generator=TemplateScriptGenerator())
    main_module.workflow = main_module.LangGraphEpisodeWorkflow(main_module.service)


def _script_episode():
    reset_cover_runtime()
    episode = client.post("/api/episodes").json()
    topics = client.post(f"/api/episodes/{episode['id']}/generate-topics").json()["topics"]
    client.post(
        f"/api/episodes/{episode['id']}/approve-topic",
        json={"topic_id": topics[0]["id"]},
    )
    return client.post(f"/api/episodes/{episode['id']}/generate-script").json()


def test_cover_endpoint_returns_editable_cover_draft():
    episode = _script_episode()

    response = client.get(f"/api/episodes/{episode['id']}/cover")

    assert response.status_code == 200
    body = response.json()
    assert body["episode_id"] == episode["id"]
    assert body["headline"]
    assert body["preview_aspect_ratio"] == "4:3"
    assert body["templates"][0]["template_id"] == "pixel_diary"


def test_cover_endpoint_updates_cover_copy_with_version_history():
    episode = _script_episode()

    response = client.post(
        f"/api/episodes/{episode['id']}/cover",
        json={
            "headline": "Agent 失控了吗",
            "subline": "不是模型坏，是没装边界",
            "tag": "AI观察",
            "template_id": "pixel_diary",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["script"]["cover_copy"] == "Agent 失控了吗\n不是模型坏，是没装边界\nAI观察"
    assert body["script_versions"][-1]["source"] == "manual_edit"
    assert body["script_versions"][-1]["reason"] == "封面编辑器更新"
