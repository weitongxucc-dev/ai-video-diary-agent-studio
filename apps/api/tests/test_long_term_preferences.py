from datetime import date

from fastapi.testclient import TestClient

import app.main as main_module
from app.models import FeedbackEvent, FeedbackValue
from app.preference_memory import LongTermPreferenceRepository
from app.repository import EpisodeRepository


client = TestClient(main_module.app)


def reset_preference_state(tmp_path):
    database_path = tmp_path / "agent.sqlite3"
    main_module.repo = EpisodeRepository(
        today_provider=lambda: date(2026, 6, 7),
        database_path=database_path,
    )
    main_module.preference_repo = LongTermPreferenceRepository(database_path)
    return main_module.repo, main_module.preference_repo


def test_preferences_endpoint_exposes_confirmable_candidates(tmp_path):
    repo, _ = reset_preference_state(tmp_path)
    episode = repo.today()
    episode.feedback_events.append(
        FeedbackEvent("topic", "topic_1", FeedbackValue.UP, "多讲真实项目")
    )
    episode.feedback_events.append(
        FeedbackEvent("script_section", "section_1", FeedbackValue.DOWN, "少用概念词")
    )
    repo.save(episode)

    response = client.get("/api/preferences")

    assert response.status_code == 200
    body = response.json()
    assert body["candidates"][0]["kind"] == "prefer"
    assert body["candidates"][0]["content"] == "多讲真实项目"
    assert body["candidates"][0]["scope"] == "topic"
    assert body["candidates"][0]["confirmed"] is False
    assert body["candidates"][1]["kind"] == "avoid"
    assert body["candidates"][1]["content"] == "少用概念词"
    assert body["long_term_preferences"] == []


def test_confirmed_preference_can_be_disabled_restored_deleted_and_injected(tmp_path):
    repo, _ = reset_preference_state(tmp_path)
    episode = repo.today()
    event = FeedbackEvent("topic", "topic_1", FeedbackValue.UP, "多讲真实项目")
    episode.feedback_events.append(event)
    repo.save(episode)

    confirm_response = client.post(
        "/api/preferences/confirm",
        json={
            "kind": "prefer",
            "content": "多讲真实项目",
            "source_event_ids": [event.id],
            "confidence": 0.65,
            "scope": "topic",
        },
    )

    assert confirm_response.status_code == 200
    confirmed = confirm_response.json()["long_term_preferences"][0]
    assert confirmed["content"] == "多讲真实项目"
    assert confirmed["enabled"] is True
    assert confirmed["deleted"] is False
    assert confirmed["source_event_ids"] == [event.id]
    assert any(
        history.reason == "多讲真实项目" and history.active
        for history in main_module.feedback_history_for("new_episode")
    )

    disabled_response = client.post(f"/api/preferences/{confirmed['id']}/disable")
    assert disabled_response.status_code == 200
    assert disabled_response.json()["long_term_preferences"][0]["enabled"] is False
    assert not any(
        history.reason == "多讲真实项目" and history.active
        for history in main_module.feedback_history_for("new_episode")
    )

    restored_response = client.post(f"/api/preferences/{confirmed['id']}/restore")
    assert restored_response.status_code == 200
    assert restored_response.json()["long_term_preferences"][0]["enabled"] is True

    deleted_response = client.post(f"/api/preferences/{confirmed['id']}/delete")
    assert deleted_response.status_code == 200
    deleted = deleted_response.json()["long_term_preferences"][0]
    assert deleted["deleted"] is True
    assert deleted["enabled"] is False
