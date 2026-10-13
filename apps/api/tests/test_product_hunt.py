import json
from pathlib import Path
import subprocess
import sys
import textwrap

import httpx

from app.config import Settings
import pytest

from app.product_hunt import ProductHuntClient, ProductHuntResponseError


@pytest.mark.parametrize("module", ["app.hermes_radar", "app.product_hunt", "app.external_sources"])
def test_source_client_works_without_obsidian_or_storage_imports(module, tmp_path):
    # Exercise each adapter in a fresh interpreter so cached imports cannot hide coupling.
    script = textwrap.dedent("""
        import importlib
        import importlib.abc
        from pathlib import Path
        import sys

        class RejectUnrelatedAdapters(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                blocked = {
                    'app.sources', 'app.source_repository', 'app.knowledge_grants',
                    'app.main', 'app.production',
                }
                if sys.argv[1] == 'app.hermes_radar':
                    blocked.add('app.source_sync')
                if fullname in blocked:
                    raise AssertionError(f'Unexpected runtime dependency: {fullname}')

        def reject_runtime_side_effects(event, args):
            if event in {'sqlite3.connect', 'subprocess.Popen', 'socket.connect'}:
                raise AssertionError(f'Unexpected runtime side effect: {event}')

        sys.meta_path.insert(0, RejectUnrelatedAdapters())
        sys.addaudithook(reject_runtime_side_effects)
        Path.rglob = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Vault scan forbidden'))
        adapter = importlib.import_module(sys.argv[1])
        from app.source_types import TopicSourceItem
        if sys.argv[1] == 'app.hermes_radar':
            from datetime import date
            items = adapter.HermesRadarSourceClient(sys.argv[2]).fetch_items(date(2026, 10, 14))
        else:
            import httpx
            if sys.argv[1] == 'app.product_hunt':
                from app.config import Settings
                transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
                    'data': {'posts': {'edges': [{'node': {
                        'id': 'fixture', 'name': 'Fixture', 'tagline': '测试',
                        'url': 'https://example.com/fixture', 'votesCount': 1,
                    }}]}}
                }))
                items = adapter.ProductHuntClient(Settings(product_hunt_token='test-only-token'),
                    transport=transport).fetch_source_items()
            else:
                transport = httpx.MockTransport(lambda request: httpx.Response(200,
                    text='<html><title>Fixture</title><article>测试正文</article></html>'))
                items = adapter.HtmlPageSourceClient(['https://example.com/fixture'],
                    transport=transport).fetch_items()
        assert len(items) == 1 and isinstance(items[0], TopicSourceItem)
        assert items[0].title == 'Fixture'
        assert 'app.sources' not in sys.modules
        if sys.argv[1] == 'app.hermes_radar':
            assert 'app.source_sync' not in sys.modules
    """)
    (tmp_path / "2026-10-14.md").write_text(
        "**Product Hunt 今日热门**\n**1. Fixture**\n🔗 https://example.com/fixture\n📝 测试正文\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-c", script, module, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
