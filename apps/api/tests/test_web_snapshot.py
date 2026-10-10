"""网页快照：完整正文、原网址、抓取时间、固定版本和旧版回查。

只用隔离临时库和本地 HTML 响应，不访问外网、不启动真实模型。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import external_sources as external_sources_module
from app.local_members import LocalMemberService
from app.source_repository import SourceRepository

WORKSPACE = "workspace-web"
_REAL_HTTPX_CLIENT = httpx.Client


class _Page:
    def __init__(self, url: str, status_code: int = 200, text: str = "") -> None:
        self.url = url
        self.status_code = status_code
        self.text = text


class _LocalTransport(httpx.BaseTransport):
    """只回本地构造的 HTML，不发真实网络请求。"""

    def __init__(self, pages: dict[str, _Page]) -> None:
        self.pages = pages

    def handle_request(self, request):  # pragma: no cover - httpx 内部调用
        page = self.pages.get(str(request.url))
        if page is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(page.status_code, text=page.text, request=request)


V1_HTML = (
    "<!doctype html><html><head><title>曜核一体机发布</title><style>.x{}</style></head>"
    "<body><nav>首页 关于</nav><article><h1>曜核一体机发布</h1>"
    "<p>第一段：设备支持本地成片。</p>"
    "<div><h2>规格</h2><p>重量 4 千克。</p><p>功耗 30 瓦。</p></div>"
    "</article><footer>页脚</footer></body></html>"
)
# V2 与 V1 的段落正文完全一样，只改了小节标题：这仍然是真实的页面变化，
# 必须被当成新版本，不能因为正文一样就命中旧版。
V2_HTML = (
    "<!doctype html><html><head><title>曜核一体机发布</title></head>"
    "<body><article><h1>曜核一体机发布</h1><p>第一段：设备支持本地成片。</p>"
    "<div><h2>详细规格</h2><p>重量 4 千克。</p><p>功耗 30 瓦。</p></div>"
    "</article></body></html>"
)
BR_HTML = (
    "<html><body><div>正文<br/>后文<p>内层段</p></div></body></html>"
)
INLINE_HTML = (
    "<html><body><article><h1>标题</h1>"
    "<p>这是一条<strong>关键</strong>规格说明。</p>"
    "<p>前半<br/>后半</p>"
    "<p>Hello<br>world</p>"
    "</article></body></html>"
)
LOGIN_HTML = "<html><body><nav>请登录</nav></body></html>"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main_module, "source_repository", SourceRepository(tmp_path / "web.sqlite3")
    )
    # 读取知识必须带已验证身份：用真实成员令牌，不拿空令牌当 owner。
    service = LocalMemberService(tmp_path / "members.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", service)
    owner, owner_token = service.join(service.create_invite_code(), "所有者")
    service.set_role(owner.member_id, "owner")
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", WORKSPACE),
    )
    return TestClient(main_module.app, headers={"X-Local-Member-Token": owner_token})


def _install_pages(monkeypatch, pages: dict[str, _Page]) -> None:
    """把网页抓取换成本地 transport：只回本地 HTML，不发真实网络请求。"""
    def client_factory(*args, **kwargs):
        kwargs["transport"] = _LocalTransport(pages)
        return _REAL_HTTPX_CLIENT(*args, **kwargs)

    monkeypatch.setattr(external_sources_module.httpx, "Client", client_factory)


def test_snapshot_keeps_full_body_url_fetch_time_and_section_locators(client, monkeypatch):
    _install_pages(monkeypatch, {"https://example.com/v1": _Page("https://example.com/v1", text=V1_HTML)})

    response = client.post("/api/source-items/link", json={"url": "https://example.com/v1"})

    assert response.status_code == 200
    item = response.json()
    assert item["source_url"] == "https://example.com/v1"
    assert item["fetched_at"] is not None
    # 完整正文：导航、样式和页脚都不算正文，但文章每一段都在。
    assert "第一段：设备支持本地成片。" in item["body"]
    assert "重量 4 千克。" in item["body"]
    assert "功耗 30 瓦。" in item["body"]
    assert "首页 关于" not in item["body"]
    assert "页脚" not in item["body"]
    assert item["content_hash"]


def test_repeated_snapshot_does_not_add_a_version_and_update_keeps_the_old_one(client, monkeypatch):
    _install_pages(
        monkeypatch,
        {
            "https://example.com/page": _Page("https://example.com/page", text=V1_HTML),
            "https://example.com/br": _Page("https://example.com/br", text=BR_HTML),
        },
    )
    first = client.post("/api/source-items/link", json={"url": "https://example.com/page"}).json()
    saved_first = client.post(
        f"/api/source-items/{first['id']}/save-to-knowledge",
        json={},
    ).json()

    repeat = client.post("/api/source-items/link", json={"url": "https://example.com/page"}).json()
    saved_repeat = client.post(
        f"/api/source-items/{repeat['id']}/save-to-knowledge",
        json={},
    ).json()

    # 相同正文不新增版本，指回已有版本。
    assert saved_repeat["id"] == saved_first["id"]
    detail = client.get(f"/api/knowledge-items/{saved_first['id']}").json()
    assert detail["version"] == "1"
    assert len(detail["versions"]) == 1
    assert detail["fetched_at"] is not None
    assert detail["source_url"] == "https://example.com/page"

    # 内容更新：另存新版本，旧版本正文和抓取时间保持不变。
    _install_pages(
        monkeypatch,
        {"https://example.com/page": _Page("https://example.com/page", text=V2_HTML)},
    )
    updated = client.post("/api/source-items/link", json={"url": "https://example.com/page"}).json()
    saved_updated = client.post(
        f"/api/source-items/{updated['id']}/save-to-knowledge",
        json={},
    ).json()

    detail = client.get(f"/api/knowledge-items/{saved_updated['id']}").json()
    assert detail["version"] == "2"
    assert len(detail["versions"]) == 2
    old = next(v for v in detail["versions"] if v["version"] == "1")
    current = next(v for v in detail["versions"] if v["version"] == "2")
    # 旧版仍是原来的小节标题，新版是改过的小节标题；正文相同但两次抓取各自留存。
    assert "规格" in old["title"] or old["title"] == "曜核一体机发布"
    assert any(section["title"] == "规格" for section in old["sections"])
    assert any(section["title"] == "详细规格" for section in current["sections"])
    assert old["content_hash"] != current["content_hash"]
    # 旧版仍带着自己当时的抓取时间和网址，能回到当时内容。
    assert old["fetched_at"] is not None and current["fetched_at"] is not None


def test_another_workspace_cannot_read_another_workspace_snapshot(client, monkeypatch):
    _install_pages(monkeypatch, {"https://example.com/v1": _Page("https://example.com/v1", text=V1_HTML)})
    item = client.post("/api/source-items/link", json={"url": "https://example.com/v1"}).json()
    saved = client.post(
        f"/api/source-items/{item['id']}/save-to-knowledge",
        json={},
    ).json()

    other = client.get(
        f"/api/knowledge-items/{saved['id']}", headers={"x-test-workspace": "workspace-other"}
    )

    assert other.status_code == 404
    assert other.json()["detail"] == "knowledge item not found"


def test_login_wall_page_fails_without_saving_a_snapshot(client, monkeypatch):
    _install_pages(
        monkeypatch,
        {
            "https://example.com/v1": _Page("https://example.com/v1", text=V1_HTML),
            "https://example.com/login": _Page("https://example.com/login", text=LOGIN_HTML),
        },
    )

    failure = client.post("/api/source-items/link", json={"url": "https://example.com/login"})

    assert failure.status_code >= 400
    assert "正文" in str(failure.json()["detail"])
    # 失败不留下可引用的信息条目。
    items = client.get("/api/source-items").json()["items"]
    assert all("example.com/login" not in (item.get("source_url") or "") for item in items)


def test_unreachable_url_is_a_real_failure_not_an_empty_success(client, monkeypatch):
    _install_pages(monkeypatch, {"https://example.com/v1": _Page("https://example.com/v1", text=V1_HTML)})

    response = client.post("/api/source-items/link", json={"url": "https://example.com/missing"})

    assert response.status_code >= 400
    detail = response.json()["detail"]
    assert detail["error"] in {"temporary_failure", "unreadable_page"}
    assert detail["run"]["status"] == "failed"
def test_inline_markup_stays_one_paragraph_and_br_keeps_text(client, monkeypatch):
    _install_pages(monkeypatch, {"https://example.com/inline": _Page("https://example.com/inline", text=INLINE_HTML)})

    item = client.post("/api/source-items/link", json={"url": "https://example.com/inline"}).json()

    texts = [section["text"] for section in item["sections"]]
    # <strong> 是行内标签：合成同一段，不虚构成多段。
    assert "这是一条关键规格说明。" in texts
    # <br/> 和普通 <br> 都不丢字：前后文字都在同一段。
    assert "前半 后半" in texts
    assert "Hello world" in texts
    assert item["body"].count("这是一条") == 1

def test_same_body_under_two_urls_stays_two_separate_origins(client, monkeypatch):
    # 两个不同网址正文完全一样：这是两条独立出处，不能合并成一条丢掉第二个网址。
    _install_pages(
        monkeypatch,
        {
            "https://a.example.com/post": _Page("https://a.example.com/post", text=V1_HTML),
            "https://b.example.com/post": _Page("https://b.example.com/post", text=V1_HTML),
        },
    )
    first_item = client.post("/api/source-items/link", json={"url": "https://a.example.com/post"}).json()
    first = client.post(
        f"/api/source-items/{first_item['id']}/save-to-knowledge",
        json={},
    ).json()
    second_item = client.post("/api/source-items/link", json={"url": "https://b.example.com/post"}).json()
    second = client.post(
        f"/api/source-items/{second_item['id']}/save-to-knowledge",
        json={},
    ).json()

    assert second["id"] != first["id"]
    assert first["source_url"] == "https://a.example.com/post"
    assert second["source_url"] == "https://b.example.com/post"
    assert second["duplicate_of_version"] is None
