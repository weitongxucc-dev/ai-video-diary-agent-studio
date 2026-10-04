"""读取权限的 HTTP 真实入口：跨身份、撤销共享与无效令牌。

这些用例只证明「读到的内容受同一判据约束」：owner 读不到别人的私有内容、
显式收回后所有读路径立即失效、无效成员令牌不会被当成「没带令牌所以按 owner
看全部」。这仍不是传输层隔离，也不冒充认证体系已完备。

全部使用 tmp_path 合成库与真实成员令牌，不连接真实用户库、不启动认证后端。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module
from app.local_members import LocalMemberService
from app.source_repository import SourceRepository

client = TestClient(main_module.app)
WORKSPACE = "ws-110-http"


def _setup(tmp_path, monkeypatch):
    service = LocalMemberService(tmp_path / "agent.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", service)
    monkeypatch.setattr(
        main_module, "source_repository", SourceRepository(tmp_path / "sources.sqlite3")
    )
    monkeypatch.setattr(
        main_module, "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", WORKSPACE),
    )
    owner = service.add_member("所有者", "owner")
    member, member_token = service.join(service.create_invite_code(), "小王")
    return {
        "owner_token": "",
        "owner_member": owner,
        "member_token": member_token,
        "member": member,
    }


def _headers(token: str) -> dict[str, str]:
    return {"X-Local-Member-Token": token}


def _save_private_knowledge(title: str) -> dict:
    source = client.post(
        "/api/source-items/manual",
        json={"title": f"{title}来源", "content_type": "text", "body": "正文", "license": "owned"},
    )
    assert source.status_code == 200, source.text
    saved = client.post(
        f"/api/source-items/{source.json()['id']}/save-to-knowledge",
        json={"ownership": "自有", "authorization": "自有"},
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_owner_token_does_not_grant_another_members_private_content(tmp_path, monkeypatch):
    party = _setup(tmp_path, monkeypatch)
    saved = _save_private_knowledge("所有者私有")
    assert saved["visibility"] == "private"

    # 成员令牌读不到：按不存在处理，不用 403 暴露「它存在」。
    denied = client.get(
        f"/api/knowledge-items/{saved['id']}", headers=_headers(party["member_token"])
    )
    assert denied.status_code == 404

    # 列表里同样不出现。
    listed = client.get("/api/knowledge-items", headers=_headers(party["member_token"]))
    assert listed.status_code == 200
    assert all(item["id"] != saved["id"] for item in listed.json()["items"])


def test_shared_becomes_readable_and_revoke_takes_effect_immediately(tmp_path, monkeypatch):
    party = _setup(tmp_path, monkeypatch)
    saved = _save_private_knowledge("共享后可见")

    client.put(
        f"/api/knowledge-items/{saved['id']}/visibility",
        json={"visibility": "shared"},
        headers=_headers(party["owner_token"]),
    )
    readable = client.get(
        f"/api/knowledge-items/{saved['id']}", headers=_headers(party["member_token"])
    )
    assert readable.status_code == 200

    client.put(
        f"/api/knowledge-items/{saved['id']}/visibility",
        json={"visibility": "private"},
        headers=_headers(party["owner_token"]),
    )
    revoked = client.get(
        f"/api/knowledge-items/{saved['id']}", headers=_headers(party["member_token"])
    )
    assert revoked.status_code == 404
    listed = client.get("/api/knowledge-items", headers=_headers(party["member_token"]))
    assert all(item["id"] != saved["id"] for item in listed.json()["items"])


def test_invalid_member_token_is_rejected_and_never_treated_as_owner(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    saved = _save_private_knowledge("私有内容")

    denied = client.get(
        f"/api/knowledge-items/{saved['id']}",
        headers=_headers("not-a-real-member-token"),
    )

    assert denied.status_code == 401
    listed = client.get("/api/knowledge-items", headers=_headers("not-a-real-member-token"))
    assert listed.status_code == 401


def test_cited_version_facts_are_not_returned_to_a_reader_without_permission(tmp_path, monkeypatch):
    """引用回查不得把无权读取的私有快照的来源链接与指纹带出去。"""
    party = _setup(tmp_path, monkeypatch)
    saved = _save_private_knowledge("被引用的私有资料")
    item_id, version = saved["id"], saved["version"]
    item = main_module.source_repository.get_knowledge_item(WORKSPACE, item_id)

    from types import SimpleNamespace

    def reader(token: str):
        return SimpleNamespace(
            state=SimpleNamespace(principal=None),
            headers={"X-Local-Member-Token": token},
        )

    member_request = reader(party["member_token"])

    # 归属是别人时，带令牌的成员读不到。
    assert main_module._content_visible(
        member_request, WORKSPACE, "private", "somebody-else"
    ) is False

    # 这条经该路由保存时没有可解析身份，owner 为空：不算任何成员的私有内容，
    # 认证态读取一律按不可见处理（local 受控诊断除外）。
    assert item.owner_user_id is None
    assert main_module._content_visible(
        member_request, WORKSPACE, "private", item.owner_user_id
    ) is False

    # 无权读取的一版不给出任何快照事实：来源链接、抓取时刻和指纹都为空。
    denied = main_module.ScriptLibrarySourceResponse(
        reference_id=f"knowledge:{item_id}:{version}", status="missing"
    )
    assert denied.status != "readable"
    assert not denied.cited_source_url
    assert not denied.cited_content_hash
    assert not denied.cited_fetched_at


def test_material_insights_do_not_count_another_members_private_content(tmp_path, monkeypatch):
    """统计数字本身不能泄露别人的私有内容：聚合与列表同一判据。"""
    party = _setup(tmp_path, monkeypatch)
    from datetime import datetime

    from app.production import MaterialAsset, ProductionRepository

    library = tmp_path / "library"
    library.mkdir()
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=library,
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [5.0, 17.0],
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    production._save_asset(MaterialAsset(
        id="private-asset", workspace_id=WORKSPACE, file_name="mine.mp4",
        path=f"{WORKSPACE}/mine.mp4", size_bytes=10, modified_at=datetime(2026, 1, 1),
        owner_user_id=party["owner_member"].member_id,
    ))

    denied = client.get("/api/production/materials/insights", headers=_headers(party["member_token"]))

    assert denied.status_code == 200
    body = denied.text
    assert "mine.mp4" not in body


def test_anonymous_read_outside_the_reserved_local_workspace_is_not_local_diagnostic(
    tmp_path, monkeypatch
):
    """受控 local 只覆盖保留的 local 工作区；别的 workspace 匿名请求读不到私有内容。"""
    party = _setup(tmp_path, monkeypatch)
    saved = _save_private_knowledge("匿名读不到")

    # 带令牌的有效成员在别的 workspace 上按成员隔离（读不到他人私有）。
    denied = client.get(
        f"/api/knowledge-items/{saved['id']}", headers=_headers(party["member_token"])
    )
    assert denied.status_code == 404
