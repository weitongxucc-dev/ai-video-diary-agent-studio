"""Direct HTTP contracts; synthetic spaces exist only in the resolver fixture.

Real LocalMember tokens are used throughout. Injected relationships prove the
grant/read/revoke mechanics, not production same-device membership or main
integration. No principal/VerifiedSession is fabricated.
"""

import json
from dataclasses import replace

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.knowledge_grant_routes import knowledge_grant_router
from app.knowledge_grants import (
    GrantTargetResolution,
    KnowledgeGrantTarget,
    VerifiedMemberGrantTargetResolver,
)
from app.local_members import LocalMemberService, LocalMembersError
from app.repository import LEGACY_WORKSPACE_ID
from app.source_repository import SourceRepository
from app.sources import KnowledgeItem, SourceConnector, SourceItem


class InjectedRelationshipFixture:
    """TEST DATA at GrantTargetResolver; never a production member mapping."""

    def __init__(self, uploader_id, reader_id):
        self.spaces = {uploader_id: "test-source-space", reader_id: "test-target-space"}

    def workspace_for_member(self, user_id):
        return self.spaces[user_id]

    def resolve(self, request, *, workspace_id, user_id):
        return GrantTargetResolution(
            status="resolved", message="TEST DATA: injected relationship, not same-device acceptance",
            targets=(KnowledgeGrantTarget("test-target-space", "隔离目标空间"),),
        )


def client_for(repo, members, resolver):
    def member_for(request: Request):
        try:
            member = members.resolve_member(request.headers.get("X-Local-Member-Token"))
        except LocalMembersError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if member is None:
            raise HTTPException(status_code=401, detail="verified member required")
        return member

    def workspace_for(request: Request):
        member = member_for(request)
        # Every synthetic namespace is sourced from the one explicitly injected
        # resolver fixture. Production LocalMember remains exclusively local.
        if isinstance(resolver, InjectedRelationshipFixture):
            return resolver.workspace_for_member(member.member_id)
        return LEGACY_WORKSPACE_ID

    app = FastAPI()
    app.include_router(knowledge_grant_router(
        repo, workspace_for=workspace_for,
        content_reader=lambda request, workspace: (member_for(request).member_id, False),
        target_resolver=resolver,
    ))
    return TestClient(app)


def test_production_missing_facts_and_local_same_space_are_explicit(tmp_path):
    members = LocalMemberService(tmp_path / "members.sqlite3")
    uploader, token = members.join(members.create_invite_code(), "上传者")
    repo = SourceRepository(tmp_path / "sources.sqlite3")
    item = repo.create_knowledge_item(KnowledgeItem(
        id="local-private", workspace_id=LEGACY_WORKSPACE_ID, content_type="text",
        title="原件", body="原文", file_reference=None, authorization="自有",
        ownership="上传者", source_item_id=None, version="1", owner_user_id=uploader.member_id,
    ))
    headers = {"X-Local-Member-Token": token}
    url = f"/api/knowledge-items/{item.id}/grants"
    resolver = VerifiedMemberGrantTargetResolver(local_mode=False)
    client = client_for(repo, members, resolver)
    unresolved = client.get(url, headers=headers)
    assert unresolved.status_code == 200
    assert unresolved.json()["target_resolution"] == "unresolved"
    assert unresolved.json()["cross_workspace_status"] == "UNVERIFIED"
    assert unresolved.json()["targets"] == []
    missing = client.post(url, headers=headers, json={"target_workspace_id": "client-invented-space"})
    assert missing.status_code == 503
    assert missing.json()["detail"]["code"] == "grant_target_unresolved"
    assert missing.json()["detail"]["target_resolution"] == "unresolved"
    assert repo.list_knowledge_grants(LEGACY_WORKSPACE_ID, item.id, user_id=uploader.member_id) == []
    _, device_owner_token = members.whoami(None)
    device_owner_headers = {"X-Local-Member-Token": device_owner_token}
    assert client.get(f"/api/knowledge-items/{item.id}/versions/1", headers=device_owner_headers).status_code == 404
    assert client.post(url, headers=device_owner_headers, json={"target_workspace_id": "client-invented-space"}).status_code == 404
    assert client.post(url, headers={"X-Local-Member-Token": "invalid-token"}, json={"target_workspace_id": "client-invented-space"}).status_code == 401
    client = client_for(repo, members, VerifiedMemberGrantTargetResolver(local_mode=True))
    local = client.get(url, headers=headers)
    assert local.json()["target_resolution"] == "same_workspace"
    assert "仅同空间" in local.json()["target_resolution_message"]
    assert local.json()["cross_workspace_status"] == "UNVERIFIED"
    assert local.json()["targets"] == []
    same = client.post(url, headers=headers, json={"target_workspace_id": LEGACY_WORKSPACE_ID})
    assert same.status_code == 409 and same.json()["detail"]["code"] == "same_workspace"
    assert "仅同空间" in same.json()["detail"]["message"]
    attempted_cross = client.post(url, headers=headers, json={"target_workspace_id": "client-invented-space"})
    assert attempted_cross.status_code == 503
    assert attempted_cross.json()["detail"]["target_resolution"] == "same_workspace"
    print(json.dumps({"production_missing": missing.status_code, "resolution": "unresolved",
                      "local_resolution": local.json()["target_resolution"],
                      "same_space": same.status_code, "local_cross_space": attempted_cross.status_code,
                      "cross_workspace_acceptance": "UNVERIFIED", "persisted_grants": 0,
                      "device_owner_private_read": 404, "invalid_token": 401}))


def test_injected_resolver_grant_fixed_read_reference_revoke_preserves_original(tmp_path):
    members = LocalMemberService(tmp_path / "members.sqlite3")
    uploader, token_a = members.join(members.create_invite_code(), "甲")
    reader, token_b = members.join(members.create_invite_code(), "乙")
    resolver = InjectedRelationshipFixture(uploader.member_id, reader.member_id)
    source_space = resolver.workspace_for_member(uploader.member_id)
    target_space = resolver.workspace_for_member(reader.member_id)
    path = tmp_path / "sources.sqlite3"
    repo = SourceRepository(path)
    repo.upsert_connector(SourceConnector(
        id="fixture-files", workspace_id=source_space, platform="knowledge_files",
        protocol="local_file", account_or_rule="用户提供", frequency="on_demand", permissions=("read:source_items",),
    ))
    source = SourceItem(
        id="source-v1", workspace_id=source_space, connector_id="fixture-files",
        source_label="file_import", source_url=None, content_type="text/markdown", author=None,
        published_at=None, body="第一版原文", subtitles=None, media_references=(), license="user_provided",
        title="资料", file_reference="资料.md", content_hash="hash-v1",
    )
    repo.save_source_items(source_space, [source])
    item = repo.save_source_item_to_knowledge(
        source_space, source.id, ownership="甲所有", authorization="允许引用",
        verified_owner_user_id=uploader.member_id,
    )
    client = client_for(repo, members, resolver)
    owner_headers, reader_headers = {"X-Local-Member-Token": token_a}, {"X-Local-Member-Token": token_b}
    url = f"/api/knowledge-items/{item.id}/grants"
    fixed_url = f"/api/knowledge-items/{item.id}/versions/1"
    before = client.get(fixed_url, headers=reader_headers)
    assert before.status_code == 404
    wrong_target = client.post(url, headers=owner_headers, json={"target_workspace_id": "not-in-resolver"})
    assert wrong_target.status_code == 503
    assert wrong_target.json()["detail"]["code"] == "grant_target_not_verified"
    assert client.post(url, headers=owner_headers, json={"target_workspace_id": target_space, "owner_user_id": reader.member_id}).status_code == 422
    granted = client.post(url, headers=owner_headers, json={"target_workspace_id": target_space})
    assert granted.status_code == 200
    grant = granted.json()
    assert grant["granted_by"] == uploader.member_id and grant["source_workspace_id"] == source_space
    assert client.post(url, headers=owner_headers, json={"target_workspace_id": target_space}).json()["id"] == grant["id"]
    repo = SourceRepository(path)
    assert [row.id for row in repo.list_knowledge_items_readable_by(target_space, user_id=reader.member_id)] == [item.id]
    assert repo.get_knowledge_item_readable_by(target_space, item.id, user_id=reader.member_id).body == source.body
    source_v2 = replace(source, id="source-v2", content_hash="hash-v2", body="第二版原文")
    repo.save_source_items(source_space, [source_v2])
    updated = repo.save_source_item_to_knowledge(
        source_space, source_v2.id, ownership="甲所有", authorization="允许引用", verified_owner_user_id=uploader.member_id,
    )
    assert updated.version == "2"
    fixed = client.get(fixed_url, headers=reader_headers)
    assert fixed.status_code == 200 and fixed.json()["body"] == "第一版原文"
    assert fixed.json()["version"] == "1" and fixed.json()["content_hash"] == "hash-v1"
    cited = repo.get_knowledge_reference_readable_by(target_space, item.id, "1", user_id=reader.member_id)
    assert cited.body == "第一版原文" and cited.workspace_id == source_space and cited.owner_user_id == uploader.member_id
    assert client.get(f"/api/knowledge-items/{item.id}/versions/absent", headers=reader_headers).status_code == 404
    recipient = client.get(url, headers=reader_headers).json()
    assert recipient["can_manage"] is False and recipient["access_via_grant"] is True
    assert recipient["targets"] == [] and recipient["grants"] == []
    assert client.post(url, headers=reader_headers, json={"target_workspace_id": source_space}).status_code == 404
    assert client.delete(f"{url}/{grant['id']}", headers=reader_headers).status_code == 404
    revoked = client.delete(f"{url}/{grant['id']}", headers=owner_headers)
    assert revoked.status_code == 200 and revoked.json()["revoked_by"] == uploader.member_id
    assert client.delete(f"{url}/{grant['id']}", headers=owner_headers).json() == revoked.json()
    after = client.get(fixed_url, headers=reader_headers)
    assert after.status_code == 404
    repo = SourceRepository(path)
    assert repo.list_knowledge_items_readable_by(target_space, user_id=reader.member_id) == []
    original = repo.get_knowledge_item(source_space, item.id)
    assert original.workspace_id == source_space and original.owner_user_id == uploader.member_id
    assert repo.get_knowledge_item_visibility(source_space, item.id) == "private"
    assert [version.version for version in repo.list_knowledge_item_versions(source_space, item.id)] == ["1", "2"]
    assert len(repo.list_knowledge_grants(source_space, item.id, user_id=uploader.member_id)) == 1
    print(json.dumps({"evidence": "injected resolver only", "before_read": before.status_code,
                      "wrong_target": wrong_target.status_code, "grant": granted.status_code,
                      "fixed_read": fixed.status_code, "fixed_version": fixed.json()["version"],
                      "fixed_hash": fixed.json()["content_hash"], "revoke": revoked.status_code,
                      "after_read": after.status_code, "after_list_count": 0,
                      "original_owner_unchanged": True, "original_visibility": "private",
                      "versions_preserved": ["1", "2"], "production_end_to_end": "UNVERIFIED"}))
