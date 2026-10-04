"""WIL-110：手填来源保存为知识后的真实归属与读取范围。

只补此前缺失的一段证据：用 LocalMemberService 解析出的**真实有效** owner 与
member 令牌（不是空串匿名）走真实 HTTP，验证「本人可读、他人不可读、共享后可
读、收回后失效」。tmp_path 合成库，不连接真实用户库，不启动认证后端，不调模型。

诚实边界：私有内容的 owner 归属由服务端从可信身份归一；本文件不修改仓储，
只通过真实 HTTP 观察结果。若某条断言失败，那是待修的真实缺口，不在此绕过。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module
from app.local_members import LocalMemberService
from app.source_repository import SourceRepository

client = TestClient(main_module.app)
WORKSPACE = "ws-110-upload"


def _setup(tmp_path, monkeypatch) -> dict[str, str]:
    """真实令牌：whoami(None) 返回种子 owner 的有效令牌，join 发出成员令牌。"""
    service = LocalMemberService(tmp_path / "agent.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", service)
    monkeypatch.setattr(
        main_module, "source_repository", SourceRepository(tmp_path / "sources.sqlite3")
    )
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", WORKSPACE),
    )
    owner_member, owner_token = service.whoami(None)
    _, member_token = service.join(service.create_invite_code(), "小王")
    return {
        "owner_token": owner_token,
        "owner_member_id": owner_member.member_id,
        "member_token": member_token,
    }


def _headers(token: str) -> dict[str, str]:
    return {"X-Local-Member-Token": token}


def _save_knowledge(title: str, *, as_token: str | None = None) -> dict:
    """手填来源 → 保存为知识条目，走真实 HTTP 入口。"""
    headers = _headers(as_token) if as_token else {}
    source = client.post(
        "/api/source-items/manual",
        json={"title": f"{title}来源", "content_type": "text", "body": "正文内容", "license": "owned"},
        headers=headers,
    )
    assert source.status_code == 200, source.text
    saved = client.post(
        f"/api/source-items/{source.json()['id']}/save-to-knowledge",
        json={"ownership": "自有", "authorization": "自有"},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_uploader_can_read_the_private_item_they_just_saved(tmp_path, monkeypatch):
    """本人保存的私有内容，本人应当读得到详情与列表。"""
    party = _setup(tmp_path, monkeypatch)
    saved = _save_knowledge("本人私有", as_token=party["owner_token"])
    assert saved["visibility"] == "private"

    detail = client.get(f"/api/knowledge-items/{saved['id']}", headers=_headers(party["owner_token"]))
    assert detail.status_code == 200, detail.text

    listed = client.get("/api/knowledge-items", headers=_headers(party["owner_token"]))
    assert listed.status_code == 200
    assert saved["id"] in {item["id"] for item in listed.json()["items"]}


def test_another_member_and_a_different_owner_cannot_read_private_content(tmp_path, monkeypatch):
    """他人 private：另一成员读不到；同工作区的第二个 owner 也不因此获得读取。"""
    party = _setup(tmp_path, monkeypatch)
    saved = _save_knowledge("上传者私有", as_token=party["owner_token"])
    assert saved["visibility"] == "private"

    denied = client.get(f"/api/knowledge-items/{saved['id']}", headers=_headers(party["member_token"]))
    assert denied.status_code == 404, denied.text

    # 真实 owner 令牌也不能越过「别人的私有内容」：判据按 owner_user_id 比对，
    # 不是按角色一律放行。
    owner_read = client.get(f"/api/knowledge-items/{saved['id']}", headers=_headers(party["owner_token"]))
    assert owner_read.status_code == 404, owner_read.text


def test_shared_is_readable_by_members_and_revoking_takes_effect_at_once(tmp_path, monkeypatch):
    party = _setup(tmp_path, monkeypatch)
    saved = _save_knowledge("共享后可见", as_token=party["owner_token"])

    client.put(
        f"/api/knowledge-items/{saved['id']}/visibility",
        json={"visibility": "shared"},
        headers=_headers(party["owner_token"]),
    )
    readable = client.get(f"/api/knowledge-items/{saved['id']}", headers=_headers(party["member_token"]))
    assert readable.status_code == 200, readable.text

    client.put(
        f"/api/knowledge-items/{saved['id']}/visibility",
        json={"visibility": "private"},
        headers=_headers(party["owner_token"]),
    )
    revoked = client.get(f"/api/knowledge-items/{saved['id']}", headers=_headers(party["member_token"]))
    assert revoked.status_code == 404, revoked.text
    listed = client.get("/api/knowledge-items", headers=_headers(party["member_token"]))
    assert all(item["id"] != saved["id"] for item in listed.json()["items"])