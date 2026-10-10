"""WIL-112：知识条目可见性的读取回读、显式共享/收回，以及成员读不到私有内容。

全部使用 tmp_path 合成库与真实成员令牌，不用 owner 绕过冒充隔离验证。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module
from app.database_migrations import CONTENT_VISIBILITY_DEFAULT, CONTENT_VISIBILITY_LEGACY
from app.local_members import LocalMemberService
from app.source_repository import SourceRepository

client = TestClient(main_module.app)


def _setup(tmp_path, monkeypatch) -> tuple[LocalMemberService, str]:
    service = LocalMemberService(tmp_path / "agent.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", service)
    monkeypatch.setattr(
        main_module, "source_repository", SourceRepository(tmp_path / "sources.sqlite3")
    )
    monkeypatch.setattr(
        main_module, "current_workspace_id", lambda request: request.headers.get("x-test-workspace", "ws")
    )
    # 保留的 local 工作区之外，匿名请求没有可信身份、读不到私有内容。
    # 这些用例因此用真实成员身份保存与读取，不靠「没带令牌 = owner」。
    owner, owner_token = service.join(service.create_invite_code(), "所有者")
    service.set_role(owner.member_id, "owner")
    _OWNER_HEADERS.clear()
    _OWNER_HEADERS.update(_member_headers(owner_token))
    member, token = service.join(service.create_invite_code(), "小王")
    return service, token


def _member_headers(token: str) -> dict[str, str]:
    return {"X-Local-Member-Token": token}


# 保存与「所有者自己回读」用真实所有者令牌：保留的 local 工作区之外，匿名请求
# 没有可信身份，按110的判据读不到私有内容，不能拿它冒充 owner。
_OWNER_HEADERS: dict[str, str] = {}



def _save_knowledge(title: str = "设备资料") -> dict:
    source = client.post(
        "/api/source-items/manual",
        json={"title": f"{title}来源", "content_type": "text", "body": "正文", "license": "owned"},
        headers=_OWNER_HEADERS,
    )
    assert source.status_code == 200, source.text
    saved = client.post(
        f"/api/source-items/{source.json()['id']}/save-to-knowledge",
        json={},
        headers=_OWNER_HEADERS,
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_new_knowledge_is_private_and_read_back_says_private(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    saved = _save_knowledge()
    assert saved["visibility"] == CONTENT_VISIBILITY_DEFAULT

    detail = client.get(f"/api/knowledge-items/{saved['id']}", headers=_OWNER_HEADERS)
    assert detail.status_code == 200
    assert detail.json()["visibility"] == CONTENT_VISIBILITY_DEFAULT

    listed = client.get("/api/knowledge-items", headers=_OWNER_HEADERS).json()
    assert listed["items"][0]["visibility"] == CONTENT_VISIBILITY_DEFAULT


def test_explicit_share_then_revoke_round_trips_through_read_back(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    saved = _save_knowledge()

    shared = client.put(
        f"/api/knowledge-items/{saved['id']}/visibility",
        json={"visibility": "shared"}, headers=_OWNER_HEADERS,
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["visibility"] == CONTENT_VISIBILITY_LEGACY
    assert client.get(f"/api/knowledge-items/{saved['id']}", headers=_OWNER_HEADERS).json()["visibility"] == CONTENT_VISIBILITY_LEGACY

    revoked = client.put(
        f"/api/knowledge-items/{saved['id']}/visibility",
        json={"visibility": "private"}, headers=_OWNER_HEADERS,
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["visibility"] == CONTENT_VISIBILITY_DEFAULT
    assert client.get(f"/api/knowledge-items/{saved['id']}", headers=_OWNER_HEADERS).json()["visibility"] == CONTENT_VISIBILITY_DEFAULT


def test_invalid_visibility_value_is_rejected(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    saved = _save_knowledge()
    response = client.put(
        f"/api/knowledge-items/{saved['id']}/visibility",
        json={"visibility": "public"}, headers=_OWNER_HEADERS,
    )
    assert response.status_code == 422


def test_member_cannot_read_private_knowledge_but_can_read_shared(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    service = main_module.local_members_service
    _, member_token = service.join(service.create_invite_code(), "小李")
    private_item = _save_knowledge("私有资料")
    shared_item = _save_knowledge("共享资料")
    assert client.put(
        f"/api/knowledge-items/{shared_item['id']}/visibility",
        json={"visibility": "shared"}, headers=_OWNER_HEADERS,
    ).status_code == 200

    member_list = client.get("/api/knowledge-items", headers=_member_headers(member_token))
    assert member_list.status_code == 200
    member_ids = {item["id"] for item in member_list.json()["items"]}
    assert shared_item["id"] in member_ids
    assert private_item["id"] not in member_ids

    assert client.get(
        f"/api/knowledge-items/{private_item['id']}", headers=_member_headers(member_token)
    ).status_code == 404
    assert client.get(
        f"/api/knowledge-items/{shared_item['id']}", headers=_member_headers(member_token)
    ).status_code == 200


def test_member_cannot_change_visibility(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    service = main_module.local_members_service
    _, member_token = service.join(service.create_invite_code(), "小张")
    saved = _save_knowledge()

    denied = client.put(
        f"/api/knowledge-items/{saved['id']}/visibility",
        json={"visibility": "shared"},
        headers=_member_headers(member_token),
    )
    assert denied.status_code == 403
    assert client.get(f"/api/knowledge-items/{saved['id']}", headers=_OWNER_HEADERS).json()["visibility"] == CONTENT_VISIBILITY_DEFAULT


def test_visibility_survives_reparse_new_version(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    first = _save_knowledge("说明书")
    assert client.put(
        f"/api/knowledge-items/{first['id']}/visibility",
        json={"visibility": "shared"}, headers=_OWNER_HEADERS,
    ).status_code == 200

    parsed = client.post(
        "/api/source-items/file",
        json={
            "filename": "说明书.md",
            "content_base64": "IyDmlbDlvJU=",
        },
        headers=_OWNER_HEADERS,
    )
    assert parsed.status_code == 200, parsed.text
    saved = client.post(
        f"/api/source-items/{parsed.json()['id']}/save-to-knowledge",
        json={},
        headers=_OWNER_HEADERS,
    )
    assert saved.status_code == 200, saved.text
    # 新版本沿用同一条目，显式设置仍为 shared。
    assert client.get(f"/api/knowledge-items/{first['id']}", headers=_OWNER_HEADERS).json()["visibility"] == CONTENT_VISIBILITY_LEGACY


def test_unknown_item_returns_404(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert client.put(
        "/api/knowledge-items/ki_missing/visibility", json={"visibility": "shared"}
    ).status_code == 404
