from __future__ import annotations

import base64
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.external_sources import FetchedWebPage
from app.knowledge_web import snapshot_web_page
from app.local_members import LocalMemberService
from app.production import (
    CreationPlanKnowledgeReference,
    CreationPlanSourceReference,
    ProductionRepository,
)
from app.source_repository import SourceRepository
from app.sources import KnowledgeItem, SourceConnector, SourceItem


@pytest.fixture
def client(tmp_path, monkeypatch):
    repository = SourceRepository(tmp_path / "sources.sqlite3")
    monkeypatch.setattr(main_module, "source_repository", repository)
    members = LocalMemberService(tmp_path / "members.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", members)
    owner, token = members.join(members.create_invite_code(), "来源合同测试成员")
    members.set_role(owner.member_id, "owner")
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace-source-contract"),
    )
    client = TestClient(
        main_module.app,
        headers={"X-Local-Member-Token": token},
    )
    client.test_members = members
    client.test_owner_id = owner.member_id
    return client


def _manual_source(client: TestClient, *, workspace: str = "workspace-source-contract") -> dict:
    response = client.post(
        "/api/source-items/manual",
        headers={"x-test-workspace": workspace},
        json={"title": "受控测试资料", "content_type": "text", "body": "仅用于API契约测试"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_manual_and_file_ingress_persist_requested_use_state(client):
    manual = client.post(
        "/api/source-items/manual",
        json={
            "title": "仅参考资料",
            "content_type": "text",
            "body": "受控正文",
            "use_state": "reference_only",
        },
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["use_state"] == "reference_only"
    assert main_module.source_repository.get_source_item(
        "workspace-source-contract", manual.json()["id"]
    ).use_state == "reference_only"

    file_import = client.post(
        "/api/source-items/file",
        json={
            "filename": "资料.txt",
            "content_base64": base64.b64encode("受控文件正文".encode()).decode(),
            "use_state": "not_for_publish",
        },
    )
    assert file_import.status_code == 200, file_import.text
    assert file_import.json()["use_state"] == "not_for_publish"
    assert main_module.source_repository.get_source_item(
        "workspace-source-contract", file_import.json()["id"]
    ).use_state == "not_for_publish"


def test_web_ingress_persists_use_state_and_defaults_to_available(client, monkeypatch):
    url = "https://example.test/article"
    snapshot = snapshot_web_page(
        url,
        "<html><head><title>受控网页</title></head><body><article><p>网页正文</p></article></body></html>",
    )

    class StubPageClient:
        def __init__(self, _urls):
            pass

        def fetch_page(self, _url):
            return FetchedWebPage(snapshot, datetime.now(UTC), url)

    monkeypatch.setattr(main_module, "HtmlPageSourceClient", StubPageClient)
    response = client.post(
        "/api/source-items/link",
        json={"url": url, "use_state": "not_for_publish"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["use_state"] == "not_for_publish"
    persisted = main_module.source_repository.get_source_item(
        "workspace-source-contract", payload["id"]
    )
    assert persisted.use_state == "not_for_publish"
    defaulted = client.post("/api/source-items/link", json={"url": url})
    assert defaulted.status_code == 200, defaulted.text
    assert defaulted.json()["use_state"] == "available"


def test_empty_save_contract_returns_trusted_version_and_retries_idempotently(client):
    source = _manual_source(client)
    response = client.post(
        f"/api/source-items/{source['id']}/save-to-knowledge",
        json={},
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["source_item_id"] == source["id"]
    assert saved["saved_source_item_id"] == source["id"]
    assert saved["version"] == "1"
    assert saved["use_state"] == "available"
    assert saved["ownership"] == saved["authorization"] == ""
    retried = client.post(f"/api/source-items/{source['id']}/save-to-knowledge", json={})
    assert retried.status_code == 200, retried.text
    assert retried.json()["id"] == saved["id"]
    main_module.source_repository.set_knowledge_item_use_state(
        "workspace-source-contract", saved["id"], "excluded"
    )
    after_restriction = client.post(
        f"/api/source-items/{source['id']}/save-to-knowledge", json={}
    )
    assert after_restriction.status_code == 200, after_restriction.text
    assert after_restriction.json()["id"] == saved["id"]
    assert after_restriction.json()["use_state"] == "excluded"
    with main_module.source_repository._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE workspace_id='workspace-source-contract'"
        ).fetchone()[0]
    assert count == 1


def test_excluded_save_receipt_confirms_registration_without_browse_visibility(client):
    source = _manual_source(client)
    main_module.source_repository.set_source_item_use_state(
        "workspace-source-contract", source["id"], "excluded"
    )
    saved = client.post(
        f"/api/source-items/{source['id']}/save-to-knowledge",
        json={},
    )

    assert saved.status_code == 200, saved.text
    saved_item = saved.json()
    assert saved_item["source_item_id"] == source["id"]
    assert saved_item["version"]
    assert saved_item["use_state"] == "excluded"
    assert saved_item["saved_source_item_id"] == source["id"]
    assert saved_item["ownership"] == saved_item["authorization"] == ""
    browse = client.get("/api/knowledge-items")
    assert browse.status_code == 200
    assert saved_item["id"] not in {item["id"] for item in browse.json()["items"]}


def test_file_hash_dedup_is_scoped_to_the_verified_member(client):
    file = {
        "filename": "相同资料.txt",
        "content_base64": base64.b64encode("同一内容".encode()).decode(),
    }
    first_source = client.post("/api/source-items/file", json=file).json()
    first_saved = client.post(f"/api/source-items/{first_source['id']}/save-to-knowledge", json={}).json()

    second_member, second_token = client.test_members.join(
        client.test_members.create_invite_code(), "另一位成员"
    )
    client.test_members.set_role(second_member.member_id, "owner")
    other = TestClient(main_module.app, headers={"X-Local-Member-Token": second_token})
    second_source = other.post("/api/source-items/file", json=file).json()
    second_saved_response = other.post(
        f"/api/source-items/{second_source['id']}/save-to-knowledge", json={}
    )
    assert second_saved_response.status_code == 200, second_saved_response.text
    second_saved = second_saved_response.json()
    assert second_saved["id"] != first_saved["id"]
    assert second_saved["saved_source_item_id"] == second_source["id"]
    assert second_saved["use_state"] == "available"
    assert main_module.source_repository.get_knowledge_item(
        "workspace-source-contract", first_saved["id"]
    ).owner_user_id == client.test_owner_id
    assert main_module.source_repository.get_knowledge_item(
        "workspace-source-contract", second_saved["id"]
    ).owner_user_id == second_member.member_id


def test_topic_inputs_allow_reference_only_unknown_license_and_block_automation_restrictions(client):
    workspace = "workspace-source-contract"
    owner_id = client.test_owner_id
    repo = main_module.source_repository
    connector = SourceConnector(
        id="topic-source-connector", workspace_id=workspace, platform="manual",
        protocol="local", account_or_rule="test", frequency="on_demand", permissions=(),
    )
    repo.upsert_connector(connector)
    source = SourceItem(
        id="topic-source", workspace_id=workspace, connector_id=connector.id,
        source_label="manual", source_url=None, content_type="text", author=None,
        published_at=None, body="可核实的来源正文", subtitles=None, media_references=(),
        license="unknown", title="来源事实",
    )
    repo.save_source_items(workspace, [source])
    knowledge = repo.create_knowledge_item_owned(
        KnowledgeItem(
            id="topic-knowledge", workspace_id=workspace, content_type="text",
            title="参考资料", body="一段事实", file_reference=None,
            authorization="", ownership="", source_item_id=None, version="1",
        ),
        verified_user_id=owner_id,
    )
    plan = SimpleNamespace(
        id="topic-plan", workspace_id=workspace,
        knowledge_references=[CreationPlanKnowledgeReference(
            id=knowledge.id, version="1", title=knowledge.title,
            authorization="", ownership="",
        )],
        source_references=[CreationPlanSourceReference(
            id=source.id, connector_id=source.connector_id, source_label=source.source_label,
            title=source.title, license=source.license,
            identity=main_module.source_item_identity(source),
        )],
    )
    task = SimpleNamespace(id="topic-task", plan_id=plan.id, owner_user_id=owner_id)
    prepared, references, _ = main_module._creation_task_topic_inputs(
        workspace, task, plan, cloud_egress=True,
    )
    assert [item.reference_id for item in prepared] == [
        f"knowledge:{knowledge.id}:1", f"source:{source.id}"
    ]
    assert {item["object_id"] for item in references} == {knowledge.id, source.id}

    repo.set_knowledge_item_use_state(workspace, knowledge.id, "not_for_automation")
    with pytest.raises(main_module._CreationTaskTopicPreflightError) as denied_knowledge:
        main_module._creation_task_topic_inputs(workspace, task, plan, cloud_egress=False)
    assert denied_knowledge.value.reason == "knowledge_use_restricted"
    repo.set_knowledge_item_use_state(workspace, knowledge.id, "available")
    repo.set_source_item_use_state(workspace, source.id, "not_for_automation")
    with pytest.raises(main_module._CreationTaskTopicPreflightError) as denied_source:
        main_module._creation_task_topic_inputs(workspace, task, plan, cloud_egress=False)
    assert denied_source.value.reason == "source_use_restricted"


def test_plan_preflight_uses_only_reference_eligible_knowledge_and_sources(
    client, tmp_path, monkeypatch,
):
    workspace = "workspace-source-contract"
    owner_id = client.test_owner_id
    production_root = tmp_path / "production"
    library = production_root / "library"
    library.mkdir(parents=True)
    production = ProductionRepository(
        database_path=production_root / "studio.sqlite3",
        video_library_path=library,
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [5.0, 17.0],
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "model_catalog", SimpleNamespace(list_statuses=lambda: []))
    repo = main_module.source_repository
    connector = SourceConnector(
        id="plan-source-connector", workspace_id=workspace, platform="manual",
        protocol="local", account_or_rule="test", frequency="on_demand", permissions=(),
    )
    repo.upsert_connector(connector)

    for state in ("available", "reference_only", "not_for_automation", "excluded"):
        item_id = f"plan-knowledge-{state}"
        repo.create_knowledge_item_owned(
            KnowledgeItem(
                id=item_id, workspace_id=workspace, content_type="text",
                title=state, body="计划预检受控正文", file_reference=None,
                authorization="", ownership="", source_item_id=None, version="1",
                use_state=state,
            ),
            verified_user_id=owner_id,
        )
        repo.save_source_items(workspace, [SourceItem(
            id=f"plan-source-{state}", workspace_id=workspace,
            connector_id=connector.id, source_label="manual", source_url=None,
            content_type="text", author=None, published_at=None,
            body="计划预检来源正文", subtitles=None, media_references=(),
            license="unknown", title=state, use_state=state,
        )])

    preview = client.post(
        "/api/creation-plans/preview",
        json={"goal": "验证资料用途", "presenter_share_target": 0},
    )
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert {item["id"] for item in payload["knowledge_references"]} == {
        "plan-knowledge-available", "plan-knowledge-reference_only",
    }
    assert {item["id"] for item in payload["source_references"]} == {
        "plan-source-available", "plan-source-reference_only",
    }
