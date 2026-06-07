from datetime import date

from fastapi.testclient import TestClient

import app.main as main_module
from app.preference_memory import LongTermPreferenceRepository
from app.repository import EpisodeRepository


client = TestClient(main_module.app)


def reset_comment_insight_state(tmp_path):
    database_path = tmp_path / "agent.sqlite3"
    main_module.repo = EpisodeRepository(
        today_provider=lambda: date(2026, 6, 8),
        database_path=database_path,
    )
    main_module.preference_repo = LongTermPreferenceRepository(database_path)
    return main_module.repo, main_module.preference_repo


def test_publish_comments_create_confirmable_preference_candidates(tmp_path):
    repo, _ = reset_comment_insight_state(tmp_path)
    episode = repo.today()
    repo.save(episode)

    response = client.post(
        f"/api/episodes/{episode.id}/comment-insights",
        json={
            "comments_text": "\n".join(
                [
                    "开头真实经历很抓人，想继续看",
                    "这个教程步骤很清楚",
                    "这条太官方了，像广告",
                    "先讲实测结论会更好",
                    "开头真实经历很抓人，想继续看",
                ]
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert {item["content"] for item in body["created_candidates"]} == {
        "喜欢真实经历开头",
        "教程类视频要步骤清楚",
        "不喜欢太官方的表达",
        "工具测评要先讲实际体验",
    }
    assert body["episode"]["feedback_events"][0]["target_type"] == "published_comment_insight"

    preferences = client.get("/api/preferences").json()
    assert {candidate["content"] for candidate in preferences["candidates"]} == {
        "喜欢真实经历开头",
        "教程类视频要步骤清楚",
        "不喜欢太官方的表达",
        "工具测评要先讲实际体验",
    }
    assert {candidate["scope"] for candidate in preferences["candidates"]} == {"publish"}
    assert all(candidate["confirmed"] is False for candidate in preferences["candidates"])


def test_comment_insights_ignore_empty_and_unexplainable_comments(tmp_path):
    repo, _ = reset_comment_insight_state(tmp_path)
    episode = repo.today()
    repo.save(episode)

    response = client.post(
        f"/api/episodes/{episode.id}/comment-insights",
        json={"comments_text": "\n\n哈哈\n1\n   "},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created_candidates"] == []
    assert body["episode"]["feedback_events"] == []
