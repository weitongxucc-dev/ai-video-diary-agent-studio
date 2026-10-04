from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.external_sources import ExternalSourceResponseError
from app.source_repository import SourceRepository
from app.sources import TopicSourceItem


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main_module, "source_repository", SourceRepository(tmp_path / "agent.sqlite3")
    )
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace-a"),
    )
    return TestClient(main_module.app)


def _create_connector(client: TestClient, platform: str = "not_implemented") -> dict:
    response = client.post(
        "/api/source-connectors",
        json={
            "platform": platform,
            "protocol": "https",
            "account_or_rule": "https://example.com/feed",
            "frequency": "hourly",
            "permissions": ["read:items"],
        },
    )
    assert response.status_code == 200
    return response.json()


def test_manual_source_and_knowledge_are_persistent_and_workspace_scoped(client):
    rejected_workspace = client.post(
        "/api/source-items/manual",
        json={
            "title": "手工研究",
            "content_type": "video",
            "body": "这是一段可追溯的手工文字。",
            "workspace_id": "forged-workspace",
        },
    )
    assert rejected_workspace.status_code == 422

    created = client.post(
        "/api/source-items/manual",
        json={
            "title": "手工研究",
            "content_type": "video",
            "body": "这是一段可追溯的手工文字。",
            "subtitles": "字幕内容",
            "media_references": ["media://clip-1"],
            "file_reference": "workspace://imports/research.mp4",
            "license": "CC-BY-4.0",
        },
    )

    assert created.status_code == 200
    source = created.json()
    assert source["workspace_id"] == "workspace-a"
    assert source["license"] == "CC-BY-4.0"

    saved = client.post(
        f"/api/source-items/{source['id']}/save-to-knowledge",
        json={
            "ownership": "user-owned",
            "authorization": "creator approved this copy",
            "version": "v2",
        },
    )
    assert saved.status_code == 200
    knowledge = saved.json()
    assert knowledge["id"] != source["id"]
    assert knowledge["source_item_id"] == source["id"]
    assert knowledge["authorization"] == "creator approved this copy"
    assert knowledge["version"] == "v2"

    rebuilt = SourceRepository(main_module.source_repository._database_path)
    main_module.source_repository = rebuilt
    assert client.get("/api/source-items").json()["total_count"] == 1
    assert client.get("/api/knowledge-items").json()["items"][0]["id"] == knowledge["id"]

    other_workspace = {"x-test-workspace": "workspace-b"}
    assert client.get("/api/source-items", headers=other_workspace).json()["items"] == []
    assert client.get("/api/knowledge-items", headers=other_workspace).json()["items"] == []
    assert client.get(
        f"/api/source-connectors/{source['connector_id']}", headers=other_workspace
    ).status_code == 404


def test_unimplemented_connector_is_not_reported_as_success(client):
    connector = _create_connector(client)

    response = client.post(f"/api/source-connectors/{connector['id']}/sync")

    assert response.status_code == 501
    error = response.json()["detail"]
    assert error["error"] == "capability_unavailable"
    assert error["run"]["status"] == "failed"
    assert error["run"]["connector_id"] == connector["id"]


@pytest.mark.parametrize(
    ("kind", "expected_status"),
    [
        ("authentication_required", 401),
        ("access_denied", 403),
        ("rate_limited", 429),
    ],
)
def test_external_source_access_failures_are_not_empty_success(
    client, monkeypatch, kind, expected_status
):
    class FailingClient:
        def __init__(self, _urls):
            pass

        def fetch_items(self):
            raise ExternalSourceResponseError(
                f"{kind} from source",
                kind=kind,
                impact="No items were imported.",
                repair_action="Fix the source account.",
                retryable=kind == "rate_limited",
            )

    monkeypatch.setattr(main_module, "HtmlPageSourceClient", FailingClient)
    connector = _create_connector(client, platform="external_urls")

    response = client.post(f"/api/source-connectors/{connector['id']}/sync")

    assert response.status_code == expected_status
    error = response.json()["detail"]
    assert error["error"] == kind
    assert error["run"]["status"] == "failed"
    assert error["repair_action"] == "Fix the source account."


def test_payloads_match_the_web_source_api_contract(client):
    manual_payload = {
        "title": "Web payload source",
        "content_type": "article",
        "source_url": "https://example.com/article",
        "body": "Source body",
        "subtitles": None,
        "media_references": [],
        "file_reference": None,
        "author": "creator",
        "license": "CC-BY-4.0",
    }
    created = client.post("/api/source-items/manual", json=manual_payload)

    assert created.status_code == 200
    source = created.json()
    assert set(source) == {
        "id", "workspace_id", "connector_id", "source_label", "source_url",
        "content_type", "author", "published_at", "body", "subtitles",
        "media_references", "license", "title", "file_reference", "created_at",
        # WIL-103：文件解析产出的内容指纹、出处和参数
        "content_hash", "sections", "parameters",
        # WIL-134：网页快照的抓取时间
        "fetched_at",
    }
    listed_sources = client.get(
        f"/api/source-items?connector_id={source['connector_id']}"
    )
    assert listed_sources.json() == {"items": [source], "total_count": 1}

    saved = client.post(
        f"/api/source-items/{source['id']}/save-to-knowledge",
        json={
            "ownership": "user-owned",
            "authorization": "creator approved",
            "version": "1",
        },
    )
    assert saved.status_code == 200
    knowledge = saved.json()
    assert set(knowledge) == {
        "id", "workspace_id", "content_type", "title", "body", "file_reference",
        "authorization", "ownership", "source_item_id", "version", "created_at",
        "content_hash", "sections", "parameters", "duplicate_of_version",
        # WIL-134：当前版本的抓取时间和原网址
        "fetched_at", "source_url",
        # WIL-105：用途分类、主题关系和真实解析状态
        "use_category", "topics", "parse_status",
    }
    assert client.get("/api/knowledge-items").json() == {
        "items": [knowledge],
        "total_count": 1,
        # WIL-105：数量与主题列表来自同一真实字段
        "category_counts": {
            "产品知识": 0, "公司文档": 0, "菜单条目": 0, "销售话术": 0, "未分类": 1,
        },
        "topics": [],
    }


def test_connector_sync_payload_matches_the_web_source_api_contract(client, monkeypatch):
    class HermesClient:
        def __init__(self, _report_dir):
            pass

        def fetch_items(self):
            return [
                TopicSourceItem(
                    source_type="Hermes Radar",
                    title="Persistent signal",
                    ref="https://example.com/signal",
                    excerpt="A source signal",
                )
            ]

    monkeypatch.setattr(main_module, "HermesRadarSourceClient", HermesClient)
    connector = _create_connector(client, platform="hermes_radar")
    assert set(connector) == {
        "id", "workspace_id", "platform", "protocol", "account_or_rule",
        "frequency", "permissions", "cursor", "connection_status", "latest_run_id",
        "created_at",
    }

    response = client.post(f"/api/source-connectors/{connector['id']}/sync")

    assert response.status_code == 200
    payload = response.json()
    assert payload["connector"]["id"] == connector["id"]
    assert set(payload["run"]) == {
        "id", "workspace_id", "connector_id", "status", "started_at", "finished_at",
        "cursor_before", "cursor_after", "item_count", "reason", "impact",
        "repair_action", "retryable",
    }
    assert payload["run"]["status"] == "succeeded"
    assert payload["run"]["item_count"] == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("platform", "  "),
        ("protocol", "  "),
        ("account_or_rule", "  "),
        ("frequency", "  "),
    ],
)
def test_connector_rejects_whitespace_required_input(client, field, value):
    payload = {
        "platform": "hermes_radar",
        "protocol": "local_file",
        "account_or_rule": "reports",
        "frequency": "daily",
        "permissions": [],
    }
    payload[field] = value

    response = client.post("/api/source-connectors", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize("field", ["ownership", "authorization", "version"])
def test_save_to_knowledge_rejects_whitespace_contract_input(client, field):
    created = client.post(
        "/api/source-items/manual",
        json={"title": "source", "content_type": "text", "body": "body"},
    )
    payload = {
        "ownership": "user-owned",
        "authorization": "approved",
        "version": "1",
    }
    payload[field] = "  "

    response = client.post(
        f"/api/source-items/{created.json()['id']}/save-to-knowledge",
        json=payload,
    )

    assert response.status_code == 422


@pytest.mark.parametrize("source_url", ["file:///tmp/source.txt", "ftp://example.com/feed"])
def test_manual_source_rejects_unsupported_url_scheme(client, source_url):
    response = client.post(
        "/api/source-items/manual",
        json={
            "title": "source",
            "content_type": "text",
            "source_url": source_url,
            "body": "body",
        },
    )

    assert response.status_code == 422
    assert "http or https" in response.json()["detail"]
