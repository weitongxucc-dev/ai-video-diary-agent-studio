from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module
from app.repository import EpisodeRepository


def test_api_responses_include_basic_security_headers():
    client = TestClient(main_module.app)

    response = client.get("/api/runtime/ping")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_mutating_api_requests_reject_non_local_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main_module,
        "repo",
        EpisodeRepository(database_path=tmp_path / "agent.sqlite3"),
    )
    remote_client = TestClient(
        main_module.app,
        client=("203.0.113.10", 50000),
    )

    response = remote_client.post("/api/episodes")

    assert response.status_code == 403
    assert response.json()["detail"] == "API write requests require a local client"
    assert main_module.repo.all() == []


def test_mutating_api_requests_require_configured_local_token(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_VIDEO_DIARY_API_TOKEN", "local-secret")
    monkeypatch.setattr(
        main_module,
        "repo",
        EpisodeRepository(database_path=tmp_path / "agent.sqlite3"),
    )
    client = TestClient(main_module.app)

    response = client.post("/api/episodes")

    assert response.status_code == 403
    assert response.json()["detail"] == "API write requests require a valid token"
    assert main_module.repo.all() == []


def test_mutating_api_requests_reject_wrong_local_token(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_VIDEO_DIARY_API_TOKEN", "local-secret")
    monkeypatch.setattr(
        main_module,
        "repo",
        EpisodeRepository(database_path=tmp_path / "agent.sqlite3"),
    )
    client = TestClient(main_module.app)

    response = client.post(
        "/api/episodes",
        headers={"X-AI-Video-Diary-Token": "wrong-secret"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "API write requests require a valid token"
    assert main_module.repo.all() == []


def test_mutating_api_requests_accept_configured_local_token(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_VIDEO_DIARY_API_TOKEN", "local-secret")
    monkeypatch.setattr(
        main_module,
        "repo",
        EpisodeRepository(database_path=tmp_path / "agent.sqlite3"),
    )
    client = TestClient(main_module.app)

    response = client.post(
        "/api/episodes",
        headers={"X-AI-Video-Diary-Token": "local-secret"},
    )

    assert response.status_code == 200
    assert response.json()["id"]
    assert len(main_module.repo.all()) == 1
