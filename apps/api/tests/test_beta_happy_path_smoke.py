import pytest

from scripts.beta_happy_path_smoke import SmokeFailure, run_smoke


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, path):
        self.calls.append(("GET", path, None))
        return self._next_response()

    def post(self, path, payload=None):
        self.calls.append(("POST", path, payload))
        return self._next_response()

    def _next_response(self):
        assert self.responses, "fake client received more calls than expected"
        return self.responses.pop(0)


def test_smoke_generates_topics_approves_topic_and_generates_script():
    client = FakeClient(
        [
            {"id": "ep-1", "topics": [], "selected_topic_id": None, "script": None},
            {
                "id": "ep-1",
                "status": "topics_ready",
                "topics": [{"id": "topic-1", "source_refs": [{"title": "Source"}]}],
            },
            {
                "id": "ep-1",
                "status": "topic_approved",
                "selected_topic_id": "topic-1",
            },
            {
                "id": "ep-1",
                "status": "script_ready",
                "script": {
                    "id": "script-1",
                    "hook": "开场钩子",
                    "sections": [
                        {"id": "s1", "title": "第一段", "content": "内容"},
                        {"id": "s2", "title": "第二段", "content": "内容"},
                    ],
                },
            },
        ]
    )

    result = run_smoke("http://127.0.0.1:8000", client)

    assert result == {
        "episode_id": "ep-1",
        "topic_id": "topic-1",
        "status": "script_ready",
        "script_sections": 2,
    }
    assert client.calls == [
        ("GET", "/api/episodes/today", None),
        ("POST", "/api/episodes/ep-1/generate-topics", None),
        ("POST", "/api/episodes/ep-1/approve-topic", {"topic_id": "topic-1"}),
        ("POST", "/api/episodes/ep-1/generate-script", None),
    ]


def test_smoke_reuses_existing_topic_selection_and_script():
    client = FakeClient(
        [
            {
                "id": "ep-1",
                "status": "script_ready",
                "topics": [{"id": "topic-1"}],
                "selected_topic_id": "topic-1",
                "script": {
                    "id": "script-1",
                    "hook": "已有脚本",
                    "sections": [{"id": "s1", "title": "第一段", "content": "内容"}],
                },
            },
        ]
    )

    result = run_smoke("http://127.0.0.1:8000", client)

    assert result["topic_id"] == "topic-1"
    assert result["status"] == "script_ready"
    assert client.calls == [("GET", "/api/episodes/today", None)]


def test_smoke_fails_when_topic_generation_returns_no_topics():
    client = FakeClient(
        [
            {"id": "ep-1", "topics": [], "selected_topic_id": None, "script": None},
            {"id": "ep-1", "status": "topics_ready", "topics": []},
        ]
    )

    with pytest.raises(SmokeFailure, match="no topics"):
        run_smoke("http://127.0.0.1:8000", client)


def test_smoke_fails_when_script_response_is_not_script_ready():
    client = FakeClient(
        [
            {"id": "ep-1", "topics": [], "selected_topic_id": None, "script": None},
            {
                "id": "ep-1",
                "status": "topics_ready",
                "topics": [{"id": "topic-1"}],
            },
            {
                "id": "ep-1",
                "status": "topic_approved",
                "selected_topic_id": "topic-1",
            },
            {
                "id": "ep-1",
                "status": "topic_approved",
                "script": {
                    "id": "script-1",
                    "hook": "开场钩子",
                    "sections": [{"id": "s1", "title": "第一段", "content": "内容"}],
                },
            },
        ]
    )

    with pytest.raises(SmokeFailure, match="script_ready"):
        run_smoke("http://127.0.0.1:8000", client)
