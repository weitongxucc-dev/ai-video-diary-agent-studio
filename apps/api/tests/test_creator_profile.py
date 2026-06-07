from datetime import date

from fastapi.testclient import TestClient

import app.main as main_module
from app.creator_profile import CreatorProfileRepository
from app.repository import EpisodeRepository


client = TestClient(main_module.app)


def test_creator_profile_endpoint_returns_defaults(tmp_path):
    main_module.creator_profile_repo = CreatorProfileRepository(tmp_path / "agent.sqlite3")

    response = client.get("/api/creator-profile")

    assert response.status_code == 200
    body = response.json()
    assert body["identity"]
    assert body["content_domains"]
    assert body["prompt_payload"]["identity"] == body["identity"]


def test_creator_profile_endpoint_trims_dedupes_and_persists(tmp_path):
    main_module.creator_profile_repo = CreatorProfileRepository(tmp_path / "agent.sqlite3")

    response = client.post(
        "/api/creator-profile",
        json={
            "identity": "  技术型个人创作者  ",
            "content_domains": [" AI 产品 ", "AI 产品", "", "自动化"],
            "tone_keywords": ["直接", "真实", "直接"],
            "preferred_phrases": ["先讲真实问题"],
            "banned_phrases": ["家人们", "太炸裂了"],
            "good_examples": ["我今天踩到一个具体的坑。"],
            "bad_examples": ["这个神器太炸裂了。"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["identity"] == "技术型个人创作者"
    assert body["content_domains"] == ["AI 产品", "自动化"]
    assert body["tone_keywords"] == ["直接", "真实"]
    assert body["prompt_payload"]["prefer"] == ["先讲真实问题"]
    assert body["prompt_payload"]["avoid"] == ["家人们", "太炸裂了"]

    persisted = client.get("/api/creator-profile").json()
    assert persisted["identity"] == "技术型个人创作者"
    assert persisted["content_domains"] == ["AI 产品", "自动化"]


def test_creator_profile_endpoint_can_clear_identity_and_lists(tmp_path):
    main_module.creator_profile_repo = CreatorProfileRepository(
        tmp_path / "agent.sqlite3"
    )
    client.post(
        "/api/creator-profile",
        json={
            "identity": "技术型个人创作者",
            "content_domains": ["AI 产品"],
            "tone_keywords": ["直接"],
            "preferred_phrases": ["先讲真实问题"],
            "banned_phrases": ["家人们"],
            "good_examples": ["我今天踩到一个具体的坑。"],
            "bad_examples": ["这个神器太炸裂了。"],
        },
    )

    response = client.post(
        "/api/creator-profile",
        json={
            "identity": "",
            "content_domains": [],
            "tone_keywords": [],
            "preferred_phrases": [],
            "banned_phrases": [],
            "good_examples": [],
            "bad_examples": [],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["identity"] == ""
    assert body["content_domains"] == []
    assert body["tone_keywords"] == []
    assert body["prompt_payload"]["identity"] == ""


def test_creator_profile_update_rejects_non_local_clients(tmp_path):
    main_module.creator_profile_repo = CreatorProfileRepository(tmp_path / "agent.sqlite3")
    remote_client = TestClient(
        main_module.app,
        client=("203.0.113.10", 50000),
    )

    response = remote_client.post(
        "/api/creator-profile",
        json={"identity": "远程改写"},
    )

    assert response.status_code == 403
    assert client.get("/api/creator-profile").json()["identity"] != "远程改写"


def test_generate_topics_route_passes_creator_profile_to_workflow(tmp_path):
    captured = {}

    class CapturingWorkflow:
        def run(self, action, episode, **kwargs):
            captured["action"] = action
            captured["creator_profile"] = kwargs.get("creator_profile")
            return episode

    main_module.repo = EpisodeRepository(
        today_provider=lambda: date(2026, 6, 7),
        database_path=tmp_path / "agent.sqlite3",
    )
    episode = main_module.repo.create()
    main_module.creator_profile_repo = CreatorProfileRepository(tmp_path / "agent.sqlite3")
    main_module.creator_profile_repo.save({"identity": "技术型个人创作者"})
    previous_workflow = main_module.workflow
    main_module.workflow = CapturingWorkflow()
    try:
        response = client.post(f"/api/episodes/{episode.id}/generate-topics")
    finally:
        main_module.workflow = previous_workflow

    assert response.status_code == 200
    assert captured["action"] == "generate_topics"
    assert captured["creator_profile"].identity == "技术型个人创作者"
