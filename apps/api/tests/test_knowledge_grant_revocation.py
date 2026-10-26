"""Wrong-space refusal and revocation semantics across every read surface.

The assistant reaches knowledge through ``GET /api/knowledge-items``
(``diary_tools.ts`` find_knowledge -> ``main.list_knowledge_items``), which serves
``list_knowledge_items_readable_by``. Search is the same list narrowed by query
text, so list, search and the assistant consume one predicate. These tests pin
that shared behaviour instead of asserting the SQL clause in prose.

Only real LocalMember tokens and the isolated injected resolver fixture are used.
Cross-space production acceptance stays UNVERIFIED: nothing here proves a
same-device target relationship.
"""

import json

import pytest

from app.local_members import LocalMemberService
from app.repository import LEGACY_WORKSPACE_ID
from app.source_repository import SourceRepository
from app.sources import SourceConnector, SourceItem

from tests.test_knowledge_grant_routes import InjectedRelationshipFixture, client_for


def build_granted_pair(tmp_path):
    """One uploader in a source space, one reader in a granted target space."""
    members = LocalMemberService(tmp_path / "members.sqlite3")
    uploader, token_a = members.join(members.create_invite_code(), "甲")
    reader, token_b = members.join(members.create_invite_code(), "乙")
    resolver = InjectedRelationshipFixture(uploader.member_id, reader.member_id)
    source_space = resolver.workspace_for_member(uploader.member_id)
    target_space = resolver.workspace_for_member(reader.member_id)
    repo = SourceRepository(tmp_path / "sources.sqlite3")
    repo.upsert_connector(SourceConnector(
        id="fixture-files", workspace_id=source_space, platform="knowledge_files",
        protocol="local_file", account_or_rule="用户提供", frequency="on_demand",
        permissions=("read:source_items",),
    ))
    repo.save_source_items(source_space, [SourceItem(
        id="source-v1", workspace_id=source_space, connector_id="fixture-files",
        source_label="file_import", source_url=None, content_type="text/markdown", author=None,
        published_at=None, body="第一版原文", subtitles=None, media_references=(),
        license="user_provided", title="资料", file_reference="资料.md", content_hash="hash-v1",
    )])
    item = repo.save_source_item_to_knowledge(
        source_space, "source-v1", ownership="甲所有", authorization="允许引用",
        verified_owner_user_id=uploader.member_id,
    )
    return {
        "members": members, "resolver": resolver, "repo": repo, "item": item,
        "uploader": uploader, "reader": reader, "token_a": token_a, "token_b": token_b,
        "source_space": source_space, "target_space": target_space,
    }


def test_wrong_space_target_is_refused_and_persists_nothing(tmp_path):
    """A client naming an unverified space gets 503, never a silent success."""
    ctx = build_granted_pair(tmp_path)
    client = client_for(ctx["repo"], ctx["members"], ctx["resolver"])
    owner = {"X-Local-Member-Token": ctx["token_a"]}
    item_id, target_space = ctx["item"].id, ctx["target_space"]

    for wrong in ("not-in-resolver", "client-invented-space"):
        response = client.post(
            f"/api/knowledge-items/{item_id}/grants", headers=owner,
            json={"target_workspace_id": wrong},
        )
        assert response.status_code == 503, (wrong, response.status_code, response.json())
        assert response.json()["detail"]["code"] == "grant_target_not_verified"
        assert response.json()["detail"]["cross_workspace_status"] == "UNVERIFIED"

    # Naming the item's own space is a different refusal: same-space needs no grant.
    own_space = client.post(
        f"/api/knowledge-items/{item_id}/grants", headers=owner,
        json={"target_workspace_id": ctx["source_space"]},
    )
    assert own_space.status_code == 409
    assert own_space.json()["detail"]["code"] == "same_workspace"

    repo = SourceRepository(tmp_path / "sources.sqlite3")
    assert repo.list_knowledge_grants(
        ctx["source_space"], item_id, user_id=ctx["uploader"].member_id,
    ) == []
    # The reader still cannot read anything, and the item's own space is unchanged.
    reader = {"X-Local-Member-Token": ctx["token_b"]}
    assert client.get(f"/api/knowledge-items/{item_id}/versions/1", headers=reader).status_code == 404
    assert repo.list_knowledge_items_readable_by(
        target_space, user_id=ctx["reader"].member_id,
    ) == []
    assert repo.get_knowledge_item(ctx["source_space"], item_id).workspace_id == ctx["source_space"]


def test_revocation_stops_every_new_read_surface(tmp_path):
    """After revoke: list, search, detail, assistant, fixed evidence, new reference."""
    ctx = build_granted_pair(tmp_path)
    client = client_for(ctx["repo"], ctx["members"], ctx["resolver"])
    owner = {"X-Local-Member-Token": ctx["token_a"]}
    reader = {"X-Local-Member-Token": ctx["token_b"]}
    item_id = ctx["item"].id
    target_space, source_space = ctx["target_space"], ctx["source_space"]
    reader_id = ctx["reader"].member_id

    grant = client.post(
        f"/api/knowledge-items/{item_id}/grants", headers=owner,
        json={"target_workspace_id": target_space},
    ).json()

    # Existing task keeps the exact version provenance it cited; that record is
    # data, not a live permission, so it must survive verbatim.
    cited = SourceRepository(tmp_path / "sources.sqlite3").get_knowledge_reference_readable_by(
        target_space, item_id, "1", user_id=reader_id,
    )
    cited_record = {"id": cited.id, "version": cited.version,
                    "content_hash": cited.content_hash, "ownership": cited.ownership}

    assert client.get(f"/api/knowledge-items/{item_id}/versions/1", headers=reader).status_code == 200
    assert [
        row.id for row in SourceRepository(tmp_path / "sources.sqlite3")
        .list_knowledge_items_readable_by(target_space, user_id=reader_id)
    ] == [item_id]

    revoked = client.delete(f"/api/knowledge-items/{item_id}/grants/{grant['id']}", headers=owner)
    assert revoked.status_code == 200

    repo = SourceRepository(tmp_path / "sources.sqlite3")
    # List == the endpoint the assistant searches; search and the assistant are
    # this same list, so one empty result covers all three.
    assert repo.list_knowledge_items_readable_by(target_space, user_id=reader_id) == []
    # Detail.
    with pytest.raises(KeyError):
        repo.get_knowledge_item_readable_by(target_space, item_id, user_id=reader_id)
    # Fixed-version evidence behind the HTTP contract.
    assert client.get(f"/api/knowledge-items/{item_id}/versions/1", headers=reader).status_code == 404
    assert client.get(f"/api/knowledge-items/{item_id}/grants", headers=reader).status_code == 404
    # A new reference must re-check today's authorization, not reuse the old one.
    with pytest.raises(KeyError):
        repo.get_knowledge_reference_readable_by(target_space, item_id, "1", user_id=reader_id)
    with pytest.raises(KeyError):
        repo.get_knowledge_version_readable_by(target_space, item_id, "1", user_id=reader_id)

    # The retained citation record still names its version, hash and owner.
    assert cited_record == {"id": item_id, "version": "1", "content_hash": "hash-v1",
                            "ownership": "甲所有"}
    # Original ownership, visibility and the version chain are untouched.
    original = repo.get_knowledge_item(source_space, item_id)
    assert original.owner_user_id == ctx["uploader"].member_id
    assert original.workspace_id == source_space
    assert repo.get_knowledge_item_visibility(source_space, item_id) == "private"
    assert [v.version for v in repo.list_knowledge_item_versions(source_space, item_id)] == ["1"]
    print(json.dumps({
        "revoked": revoked.status_code,
        "list_after_revoke": 0, "detail_after_revoke": "KeyError",
        "assistant_and_search_after_revoke": 0,
        "fixed_version_after_revoke": 404,
        "new_reference_after_revoke": "KeyError",
        "retained_citation": cited_record,
        "original_owner_unchanged": True, "original_visibility": "private",
        "versions_preserved": ["1"], "cross_workspace_acceptance": "UNVERIFIED",
    }))


def test_local_mode_refuses_every_cross_space_grant_without_storing(tmp_path):
    """Local mode is same-workspace only; nothing may be stored on any path."""
    from app.knowledge_grants import VerifiedMemberGrantTargetResolver

    members = LocalMemberService(tmp_path / "members.sqlite3")
    owner, token = members.join(members.create_invite_code(), "甲")
    other, _ = members.join(members.create_invite_code(), "乙")
    repo = SourceRepository(tmp_path / "sources.sqlite3")
    item = repo.create_knowledge_item(__import__("app.sources", fromlist=["KnowledgeItem"]).KnowledgeItem(
        id="local-item", workspace_id=LEGACY_WORKSPACE_ID, content_type="text",
        title="原件", body="原文", file_reference=None, authorization="自有",
        ownership="甲", source_item_id=None, version="1", owner_user_id=owner.member_id,
    ))
    headers = {"X-Local-Member-Token": token}
    url = f"/api/knowledge-items/{item.id}/grants"
    client = client_for(repo, members, VerifiedMemberGrantTargetResolver(local_mode=True))

    same_space = client.post(url, headers=headers, json={"target_workspace_id": LEGACY_WORKSPACE_ID})
    assert same_space.status_code == 409
    cross = client.post(url, headers=headers, json={"target_workspace_id": "any-other-space"})
    assert cross.status_code == 503
    assert cross.json()["detail"]["target_resolution"] == "same_workspace"
    assert repo.list_knowledge_grants(LEGACY_WORKSPACE_ID, item.id, user_id=owner.member_id) == []
    # A second member in the same local space cannot read a private original.
    _, other_token = members.join(members.create_invite_code(), "丙")
    assert client.get(
        f"/api/knowledge-items/{item.id}/versions/1",
        headers={"X-Local-Member-Token": other_token},
    ).status_code == 404
    assert repo.list_knowledge_items_readable_by(
        LEGACY_WORKSPACE_ID, user_id=other.member_id,
    ) == []
    print(json.dumps({"same_space": same_space.status_code, "cross_space": cross.status_code,
                      "persisted_grants": 0, "other_member_private_read": 404,
                      "cross_workspace_acceptance": "UNVERIFIED"}))


def test_main_consumers_read_original_space_and_recheck_fixed_reference_grants(tmp_path, monkeypatch):
    """Synthetic relationship proves main adapters, never real workspace membership."""
    from dataclasses import replace
    from fastapi.testclient import TestClient
    import app.main as main
    from app.creation.script_library_use_case import ScriptLibrarySource
    from app.production import CreationTask, ProductionRepository

    ctx = build_granted_pair(tmp_path)
    repo, item = ctx["repo"], ctx["item"]
    source_space, target_space = ctx["source_space"], ctx["target_space"]
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    production = ProductionRepository(tmp_path / "production.sqlite3", inbox)
    monkeypatch.setattr(main, "source_repository", repo)
    monkeypatch.setattr(main, "production_repo", production)
    monkeypatch.setattr(main, "local_members_service", ctx["members"])
    monkeypatch.setattr(main, "current_workspace_id", lambda request: target_space)
    headers = {"X-Local-Member-Token": ctx["token_b"]}
    grant = repo.grant_knowledge_item(source_space, item.id, target_space, user_id=ctx["uploader"].member_id)
    source = repo.get_source_item(source_space, "source-v1")
    repo.save_source_items(source_space, [replace(source, id="source-v2", body="第二版原文", content_hash="hash-v2")])
    repo.save_source_item_to_knowledge(
        source_space, "source-v2", ownership=item.ownership, authorization=item.authorization,
        verified_owner_user_id=ctx["uploader"].member_id,
    )
    client = TestClient(main.app)
    listed = client.get("/api/knowledge-items", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["workspace_id"] == source_space
    detail = client.get(f"/api/knowledge-items/{item.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["visibility"] == "private"
    assert [v["version"] for v in detail.json()["versions"]] == ["1", "2"]
    payload = {"goal": "核对授权资料", "name": "授权消费者", "target_count": 1,
               "language": "zh-CN", "aspect_ratio": "9:16", "duration_target_seconds": 60}
    preview = client.post("/api/creation-plans/preview", headers=headers, json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["knowledge_references"][0]["id"] == item.id
    from starlette.requests import Request
    request = Request({
        "type": "http", "headers": [(b"x-local-member-token", ctx["token_b"].encode())],
    })
    plan = main._creation_plan_preflight(request, target_space, main.CreationPlanPreviewRequest(**payload))
    plan.knowledge_references[0] = replace(plan.knowledge_references[0], version="1")
    task = CreationTask(id="fixed-task", workspace_id=target_space, plan_id=plan.id,
                        name="固定引用", scenario="knowledge_explanation", owner_user_id=ctx["reader"].member_id)
    # Read-only input assembly: no model, worker or production write.
    provider_sources, references, _ = main._creation_task_topic_inputs(target_space, task, plan, cloud_egress=False)
    assert provider_sources[0].text == "第一版原文"
    assert references[0]["version_or_identity"] == "1"
    facts = ScriptLibrarySource(reference_id=f"knowledge:{item.id}:1", status="readable")
    main._script_library_use_case(request)._attach_cited_version_facts(
        facts, detail.json()["workspace_id"], item.id, "1")
    assert facts.cited_version == "1" and facts.cited_content_hash == "hash-v1"
    repo.revoke_knowledge_grant(source_space, item.id, grant.id, user_id=ctx["uploader"].member_id)
    assert client.get("/api/knowledge-items", headers=headers).json()["items"] == []
    assert client.get(f"/api/knowledge-items/{item.id}", headers=headers).status_code == 404
    assert client.post("/api/creation-plans/preview", headers=headers, json=payload).json()["knowledge_references"] == []
    with pytest.raises(main._CreationTaskTopicPreflightError) as blocked:
        main._creation_task_topic_inputs(target_space, task, plan, cloud_egress=False)
    assert blocked.value.reason == "knowledge_reference_missing"
    client.close()
    assert repo.get_knowledge_item(source_space, item.id).owner_user_id == ctx["uploader"].member_id


def test_main_mounted_local_routes_report_unavailable_and_store_no_grants(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.sources import KnowledgeItem

    members = LocalMemberService(tmp_path / "members.sqlite3")
    uploader, token = members.join(members.create_invite_code(), "本地上传者")
    monkeypatch.setattr(main, "local_members_service", members)
    # The router and main endpoints share the import-time repository, already
    # isolated by conftest before app.main import. Do not replace the router.
    repo = main.source_repository
    item = repo.create_knowledge_item(KnowledgeItem(
        id="main-mounted-local-grant", workspace_id=LEGACY_WORKSPACE_ID, content_type="text",
        title="本地原件", body="固定原文", file_reference=None, authorization="自有",
        ownership="本地上传者", source_item_id=None, version="1", owner_user_id=uploader.member_id,
    ))
    headers = {"X-Local-Member-Token": token}
    client = TestClient(main.app)
    url = f"/api/knowledge-items/{item.id}/grants"
    state = client.get(url, headers=headers)
    assert state.status_code == 200 and state.json()["target_resolution"] == "same_workspace"
    assert state.json()["cross_workspace_status"] == "UNVERIFIED"
    refused = client.post(url, headers=headers, json={"target_workspace_id": "unverified-space"})
    assert refused.status_code == 503
    assert client.post(url, headers=headers, json={"target_workspace_id": LEGACY_WORKSPACE_ID}).status_code == 409
    fixed = client.get(f"/api/knowledge-items/{item.id}/versions/1", headers=headers)
    assert fixed.status_code == 200 and fixed.json()["body"] == "固定原文"
    # Existing controlled local diagnostic reads stay consistent with detail/list.
    assert client.get(f"/api/knowledge-items/{item.id}/versions/1").status_code == 200
    assert client.get(f"/api/knowledge-items/{item.id}/versions/absent", headers=headers).status_code == 404
    client.close()
    assert repo.list_knowledge_grants(LEGACY_WORKSPACE_ID, item.id, user_id=uploader.member_id) == []
