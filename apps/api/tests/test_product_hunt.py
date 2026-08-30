import json

import httpx

from app.config import Settings
import pytest

from app.product_hunt import ProductHuntClient, ProductHuntResponseError


def test_product_hunt_client_fetches_posts_with_graphql_token():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {
                    "posts": {
                        "edges": [
                            {
                                "node": {
                                    "id": "post_1",
                                    "name": "AgentFlow",
                                    "tagline": "Build reliable AI workflows",
                                    "url": "https://www.producthunt.com/posts/agentflow",
                                    "votesCount": 128,
                                }
                            }
                        ]
                    }
                }
            },
        )

    client = ProductHuntClient(
        Settings(product_hunt_token="token"),
        transport=httpx.MockTransport(handler),
    )

    posts = client.fetch_posts(limit=5)

    assert posts[0].name == "AgentFlow"
    assert posts[0].tagline == "Build reliable AI workflows"
    assert posts[0].votes_count == 128
    assert captured["url"] == "https://api.producthunt.com/v2/api/graphql"
    assert captured["authorization"] == "Bearer token"
    assert "posts(first: 5)" in captured["payload"]["query"]


def test_settings_status_reports_product_hunt_token_without_exposing_it():
    settings = Settings(product_hunt_token="token")

    status = settings.public_status()

    assert status["product_hunt"]["configured"] is True
    assert status["product_hunt"]["api_url"] == "https://api.producthunt.com/v2/api/graphql"
    assert "token" not in json.dumps(status, ensure_ascii=False)


def test_product_hunt_rate_limit_has_explicit_retry_semantics():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"errors": [{"message": "slow down"}]})

    client = ProductHuntClient(
        Settings(product_hunt_token="token"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProductHuntResponseError) as failure:
        client.fetch_posts()

    assert failure.value.kind == "rate_limited"
    assert failure.value.retryable is True
    assert "Wait for the platform limit window" in failure.value.repair_action


def test_system_status_response_includes_external_source_urls():
    from app.schemas import SystemStatusResponse

    settings = Settings(topic_source_urls=("https://watcha.cn/",))

    status = SystemStatusResponse.model_validate(settings.public_status())

    assert status.external_sources.configured is True
    assert status.external_sources.urls == ["https://watcha.cn/"]
