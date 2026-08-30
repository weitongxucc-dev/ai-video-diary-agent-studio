from datetime import date

from fastapi.testclient import TestClient

import app.main as main_module
from app.models import EpisodeStatus
from app.repository import EpisodeRepository


client = TestClient(main_module.app)


def test_retired_voice_command_routes_are_explicit_and_have_no_episode_side_effect(tmp_path):
    main_module.repo = EpisodeRepository(
        database_path=str(tmp_path / "agent.sqlite3"),
        today_provider=lambda: date(2026, 8, 29),
    )
    episode = main_module.repo.today()

    for action in ("preview", "execute"):
        response = client.post(
            f"/api/episodes/{episode.id}/voice-command/{action}",
            json={"transcript": "继续今天的流程"},
        )

        assert response.status_code == 410
        detail = response.json()["detail"]
        assert detail["error"] == "legacy_voice_command_retired"
        assert detail["reason"]
        assert detail["impact"]
        assert detail["repair_action"]

    persisted = main_module.repo.get(episode.id)
    assert persisted is not None
    assert persisted.status == EpisodeStatus.CREATED
    assert persisted.topics == []
    assert persisted.script is None


def test_retired_voice_command_route_does_not_reveal_episode_existence():
    response = client.post(
        "/api/episodes/not-visible/voice-command/execute",
        json={"transcript": "生成剪映草稿"},
    )

    assert response.status_code == 410
    assert response.json()["detail"]["error"] == "legacy_voice_command_retired"
