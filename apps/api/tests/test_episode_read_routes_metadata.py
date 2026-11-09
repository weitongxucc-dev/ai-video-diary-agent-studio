"""Historical read views on in-memory metadata; not history URL or media acceptance."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.repository as repository_module
from app.models import (Episode, EpisodeStatus, ProductionChainStage, ProductionChainStep,
                        ScriptPackage, SourceFileState, SourceRef, TopicCandidate)
from app.repository import EpisodeRepository


def test_episode_metadata_reads_canonical_history_and_refusals_preserve_state(monkeypatch):
    execution_calls = []

    def forbidden_execution(*args, **kwargs):
        execution_calls.append(True)
        pytest.fail("metadata reads must not execute runtime or generate an edit plan")

    monkeypatch.setattr(main, "_creation_runtime", forbidden_execution)
    monkeypatch.setattr(main, "build_edit_plan_workspace", forbidden_execution)
    monkeypatch.setattr(repository_module, "active_workspace_id", lambda: "local")
    repository = EpisodeRepository()
    when = datetime(2026, 10, 29, tzinfo=timezone.utc)
    older = Episode("older", 1, date(2026, 10, 28), EpisodeStatus.CREATED,
                    created_at=when, updated_at=when,
                    script=ScriptPackage("script-old", "保留原优先级", [], [], "", "", ""))
    topic = TopicCandidate("topic", "选题标题", "观点", "理由", "读者", [], 80,
                           [SourceRef("manual", "来源标题", "source:original")])
    newer = Episode("newer", 2, date(2026, 10, 29), EpisodeStatus.PUBLISHED,
                    created_at=when + timedelta(seconds=1), updated_at=when + timedelta(seconds=1),
                    topics=[topic], selected_topic_id=topic.id,
                    script=ScriptPackage("script-new", "开头", [], ["备用标题"], "", "", ""),
                    video_production_chain=[ProductionChainStep(ProductionChainStage.COVER, status="pending")],
                    source_file_states={"source.mp4": SourceFileState.PENDING})
    foreign = replace(newer, id="foreign", workspace_id="foreign")
    repository._episodes = {ep.id: ep for ep in (older, newer, foreign)}
    monkeypatch.setattr(main, "repo", repository)
    before = deepcopy(repository._episodes)
    client = TestClient(main.app)
    listing = client.get("/api/episodes", params={"limit": 1, "offset": 0})
    assert listing.status_code == 200
    assert listing.json()["total"] == 2
    assert [ep["id"] for ep in listing.json()["episodes"]] == ["newer"]
    filtered = client.get("/api/episodes", params={"status": "created"}).json()
    assert filtered["total"] == 1 and filtered["episodes"][0]["id"] == "older"
    detail = client.get("/api/episodes/newer")
    assert detail.status_code == 200 and detail.json()["selected_topic_id"] == topic.id
    chain = client.get("/api/episodes/newer/production-chain").json()
    assert chain["episode_id"] == newer.id
    assert [(step["stage"], step["status"]) for step in chain["chain"]] == [("cover", "pending")]
    assert client.get("/api/episodes/newer/source-file-states").json() == {
        "episode_id": "newer", "files": [{"file_name": "source.mp4", "state": "pending"}],
    }
    assert client.get("/api/episodes/newer/topic-evidence").json() == {
        "episode_id": "newer", "total": 1,
        "items": [{"source_key": "source:original", "source_type": "manual", "title": "来源标题", "ref": "source:original",
                   "excerpt": None, "freshness": "recent", "freshness_label": "近期",
                   "participated_topics": 0, "last_used_at": None}],
    }
    assert client.get("/api/episodes/older/topic-evidence").json()["items"] == []
    for path in ("/api/episodes/missing", "/api/episodes/foreign",
                 "/api/episodes/older/retake-report", "/api/retake-reports/latest"):
        assert client.get(path).status_code == 404
    # Original /episodes/{episode_id} precedes /history; do not claim URL coverage.
    router = main._episode_read_http_router(("list_episode_history",))
    history_for = next(route.endpoint for route in router.routes if route.name == "list_episode_history")
    history = history_for(limit=1, offset=0, status="published")
    assert (history.total_count, history.filtered_count) == (2, 1)
    assert history.items[0].id == "newer" and history.items[0].title == topic.title
    assert history.items[0].script_title == "开头"
    fallback = history_for(limit=1, offset=1, status="not-a-real-status")
    assert (fallback.total_count, fallback.filtered_count) == (2, 2)
    assert fallback.items[0].id == "older" and fallback.items[0].title == "第1期 2026-10-28"
    assert fallback.items[0].script_title is None
    assert execution_calls == []
    assert repository._episodes == before
