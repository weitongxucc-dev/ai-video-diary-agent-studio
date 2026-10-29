from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.external_sources import ExternalSourceResponseError
from app.local_members import LocalMemberService
from app.source_repository import SourceRepository
from app.sources import TopicSourceItem


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main_module, "source_repository", SourceRepository(tmp_path / "sources.sqlite3")
    )
    # 读取信息源与知识必须带已验证身份：用真实成员令牌，不拿空令牌当 owner。
    service = LocalMemberService(tmp_path / "agent.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", service)
    owner, owner_token = service.join(service.create_invite_code(), "所有者")
    service.set_role(owner.member_id, "owner")
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace-a"),
    )
    return TestClient(main_module.app, headers={"X-Local-Member-Token": owner_token})


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


def test_knowledge_correction_is_owned_versioned_and_does_not_modify_source(client):
    source = client.post("/api/source-items/manual", json={
        "title": "验证资料", "content_type": "text", "body": "原始内容", "use_state": "available",
    }).json()
    saved = client.post(f"/api/source-items/{source['id']}/save-to-knowledge", json={}).json()
    route = f"/api/knowledge-items/{saved['id']}/content"
    payload = {"expected_version": "1", "title": "修订资料", "body": "# 修订\n- 重量: 20 kg"}
    changed = client.put(route, json=payload)
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == "2"
    versions = {item["version"]: item for item in changed.json()["versions"]}
    assert versions["1"]["body"] == "原始内容"
    assert versions["2"]["parameters"][0]["value"] == "20 kg"
    assert main_module.source_repository.get_source_item("workspace-a", source["id"]).body == "原始内容"
    assert client.put(route, json=payload).status_code == 409
    assert client.put(route, json={**payload, "expected_version": "2", "body": " "}).status_code == 422
    assert client.put(route, json={**payload, "workspace_id": "forged"}).status_code == 422
    assert client.put(route, json=payload, headers={"x-test-workspace": "workspace-b"}).status_code == 404
    service = main_module.local_members_service
    _, token = service.join(service.create_invite_code(), "另一成员")
    assert client.put(route, json=payload, headers={"X-Local-Member-Token": token}).status_code == 403
    assert client.get(f"/api/knowledge-items/{saved['id']}").json()["version"] == "2"


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
        json={},
    )
    assert saved.status_code == 200
    knowledge = saved.json()
    assert knowledge["id"] != source["id"]
    assert knowledge["source_item_id"] == source["id"]
    assert knowledge["authorization"] == ""
    assert knowledge["ownership"] == ""
    assert knowledge["version"] == "1"
    assert knowledge["use_state"] == "available"
    assert knowledge["saved_source_item_id"] == source["id"]

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


def test_unimplemented_connector_is_not_reported_as_success(client, monkeypatch):
    connector = _create_connector(client)

    response = client.post(f"/api/source-connectors/{connector['id']}/sync")

    assert response.status_code == 501
    error = response.json()["detail"]
    assert error["error"] == "capability_unavailable"
    assert error["run"]["status"] == "failed"
    assert error["run"]["connector_id"] == connector["id"]

    run = error["run"]
    database = main_module.source_repository._database_path
    monkeypatch.setattr(main_module, "source_repository", SourceRepository(database))
    base = f"/api/source-connectors/{connector['id']}"
    assert client.get(base).json()["connection_status"] == "capability_unavailable"
    assert client.get("/api/source-connectors").json()["total_count"] == 1
    assert client.get(base + "/runs").json() == {"items": [run], "total_count": 1}
    assert client.get(base + "/runs/latest").json() == run
    assert client.get(base + f"/runs/{run['id']}").json() == run
    status = client.get("/api/sources/sync-status").json()["statuses"]
    assert len(status) == 1
    assert status[0]["configured"] is False
    assert status[0]["last_success_at"] is None
    assert status[0]["last_sync_at"] == run["finished_at"]
    assert status[0]["last_failure_at"] == run["finished_at"]
    assert status[0]["error_message"] == run["reason"]
    other = _create_connector(client, platform="manual")
    assert client.get(f"/api/source-connectors/{other['id']}/runs/latest").status_code == 404
    assert client.get(f"/api/source-connectors/{other['id']}/runs/{run['id']}").status_code == 404
    foreign = {"x-test-workspace": "workspace-b"}
    assert client.get("/api/source-connectors", headers=foreign).json() == {"items": [], "total_count": 0}
    assert client.get("/api/sources/sync-status", headers=foreign).json() == {"statuses": []}
    for suffix in ("", "/runs", "/runs/latest", f"/runs/{run['id']}"):
        assert client.get(base + suffix, headers=foreign).status_code == 404
    assert main_module.source_repository.list_source_items("workspace-a") == []
    assert len(main_module.source_repository.list_runs("workspace-a", connector["id"])) == 1


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
        "fetched_at", "use_state",
    }
    listed_sources = client.get(
        f"/api/source-items?connector_id={source['connector_id']}"
    )
    assert listed_sources.json() == {"items": [source], "total_count": 1}

    saved = client.post(
        f"/api/source-items/{source['id']}/save-to-knowledge",
        json={},
    )
    assert saved.status_code == 200
    knowledge = saved.json()
    assert set(knowledge) == {
        "id", "workspace_id", "content_type", "title", "body", "file_reference",
        "authorization", "ownership", "source_item_id", "version", "created_at",
        "content_hash", "sections", "parameters", "duplicate_of_version",
        "use_state", "saved_source_item_id",
        # WIL-134：当前版本的抓取时间和原网址
        "fetched_at", "source_url",
        # WIL-112：可见性回读同一列
        "visibility",
    }
    listed = client.get("/api/knowledge-items").json()
    assert listed["total_count"] == 1
    assert len(listed["items"]) == 1
    assert listed["items"][0]["id"] == knowledge["id"]
    # 来源绑定只属于本次保存回执，不冒充列表对象本身的来源字段。
    assert listed["items"][0]["saved_source_item_id"] is None


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
def test_save_to_knowledge_rejects_retired_caller_identity_fields(client, field):
    created = client.post(
        "/api/source-items/manual",
        json={"title": "source", "content_type": "text", "body": "body"},
    )
    response = client.post(
        f"/api/source-items/{created.json()['id']}/save-to-knowledge",
        json={field: "forged caller value"},
    )

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any(
        error["type"] == "extra_forbidden" and error["loc"] == ["body", field]
        for error in errors
    )


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
