"""WIL-110：目录入库素材的真实上传者归属与读取范围。

按 auth §9「私有对象保留原空间和上传者」验证真实用户结果：目录先由甲绑定后，
乙导入的新素材必须归本次有效上传者乙且乙本人可读；甲（含 owner 角色）不能因为
先绑定目录就读到乙的私有内容；甲自己的既有私有素材不因乙重新扫描或重新绑定而
变成乙的；同一链接重复导入不得把别人的私有详情带出来。

只用 LocalMemberService 解析出的真实有效 owner/member 令牌与 tmp 合成目录，走
真实 HTTP 入口（含 126 的复制完成确认门）。不空令牌、不直接写库造 owner、不调
模型、不连真实库、不开 GUI；链接导入只 mock 网络取字节，断言的是归属与读取判据。
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.local_members import LocalMemberService
from app.material_ingestion import MaterialIngestionService
from app.production import ProductionRepository

client = TestClient(main_module.app)
WORKSPACE = "ws-110-material"
SUPPORTED = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
PUBLIC = "93.184.216.34"
IMAGE_URL = "https://media.example.com/still.png"


def _setup(tmp_path, monkeypatch) -> dict[str, str]:
    service = LocalMemberService(tmp_path / "agent.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", service)
    library = tmp_path / "inbox"
    library.mkdir()
    repository = ProductionRepository(
        database_path=tmp_path / "production.sqlite3",
        video_library_path=library,
        # 只用归属与读取判据，不做真实媒体分析。
        duration_reader=lambda path: 1.0,
        scene_reader=lambda path, seconds: [],
    )
    monkeypatch.setattr(main_module, "production_repo", repository)
    monkeypatch.setattr(main_module, "material_ingestion", MaterialIngestionService(repository))
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", WORKSPACE),
    )
    monkeypatch.setattr(main_module, "material_execution_mode", "legacy")
    # 直链导入：只替换出网传输与域名解析（返回公开地址，隔离 DNS），其余走产品代码。
    state = SimpleNamespace(handler=None)

    def client_factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(state.handler), follow_redirects=False)

    def service_factory(**kwargs):
        from app.media_link_import import MediaLinkImportService

        return MediaLinkImportService(
            **{**kwargs, "client_factory": client_factory, "resolver": lambda _host: [PUBLIC]}
        )

    monkeypatch.setattr(main_module, "MediaLinkImportService", service_factory)

    owner_member, owner_token = service.whoami(None)
    member_a, token_a = service.join(service.create_invite_code(), "甲")
    member_b, token_b = service.join(service.create_invite_code(), "乙")
    return {
        "owner_token": owner_token,
        "owner_member_id": owner_member.member_id,
        "token_a": token_a,
        "member_a_id": member_a.member_id,
        "token_b": token_b,
        "member_b_id": member_b.member_id,
        "library": str(library),
        "link_state": state,
    }


def _headers(token: str) -> dict[str, str]:
    return {"X-Local-Member-Token": token}


def _bind(token: str, library: str) -> None:
    response = client.post(
        "/api/production/materials/intake/bind",
        json={"expected_directory": library},
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text


def _confirm_pending_copies(token: str, source: str = "manual") -> None:
    """按 126 的复制完成确认门，逐个确认当前待入库文件（真实 HTTP）。"""
    status = client.get("/api/production/materials/intake", headers=_headers(token))
    assert status.status_code == 200, status.text
    for pending in status.json()["pending_files"]:
        confirmed = client.post(
            "/api/production/materials/intake/copy-complete",
            json={**pending, "confirmation_source": source},
            headers=_headers(token),
        )
        assert confirmed.status_code == 200, confirmed.text


def _drop(name: str, library: str) -> None:
    (Path(library) / name).write_bytes(SUPPORTED)


def _scan(token: str) -> None:
    _confirm_pending_copies(token)
    response = client.post("/api/production/materials/scan", headers=_headers(token))
    assert response.status_code == 200, response.text


def _asset_by_file(file_name: str, token: str) -> dict:
    listed = client.get("/api/production/materials", headers=_headers(token))
    assert listed.status_code == 200, listed.text
    for asset in listed.json()["assets"]:
        if asset["file_name"] == file_name:
            return asset
    raise AssertionError(f"素材库响应里没有 {file_name}")


def test_asset_imported_by_second_member_belongs_to_that_member(tmp_path, monkeypatch):
    """目录由甲绑定，乙确认并扫描入库的新素材必须归乙，乙本人可读。"""
    party = _setup(tmp_path, monkeypatch)
    _bind(party["token_a"], party["library"])
    _drop("乙导入的素材.mp4", party["library"])

    _scan(party["token_b"])

    asset = _asset_by_file("乙导入的素材.mp4", party["token_b"])
    assert asset["visibility"] == "private"
    detail = client.get(
        f"/api/production/materials/{asset['id']}", headers=_headers(party["token_b"])
    )
    assert detail.status_code == 200, detail.text


def test_binder_cannot_read_another_members_private_material_even_as_owner(tmp_path, monkeypatch):
    """先绑定目录的甲（含 owner 角色令牌）读不到乙的私有素材。"""
    party = _setup(tmp_path, monkeypatch)
    _bind(party["token_a"], party["library"])
    _drop("乙的私有素材.mp4", party["library"])
    _scan(party["token_b"])

    asset = _asset_by_file("乙的私有素材.mp4", party["token_b"])

    denied_for_binder = client.get(
        f"/api/production/materials/{asset['id']}", headers=_headers(party["token_a"])
    )
    assert denied_for_binder.status_code == 404, denied_for_binder.text

    # owner 角色不因角色本身获得别人的私有原件（auth §9）。
    denied_for_owner = client.get(
        f"/api/production/materials/{asset['id']}", headers=_headers(party["owner_token"])
    )
    assert denied_for_owner.status_code == 404, denied_for_owner.text

    listed_for_binder = client.get("/api/production/materials", headers=_headers(party["token_a"]))
    assert listed_for_binder.status_code == 200
    assert all(item["id"] != asset["id"] for item in listed_for_binder.json()["assets"])


def test_existing_private_material_keeps_its_original_uploader(tmp_path, monkeypatch):
    """甲的既有私有素材不因乙再次扫描或重新绑定目录而变成乙的。"""
    party = _setup(tmp_path, monkeypatch)
    _bind(party["token_a"], party["library"])
    _drop("甲的素材.mp4", party["library"])
    _scan(party["token_a"])
    original = _asset_by_file("甲的素材.mp4", party["token_a"])

    # 乙再扫一次，并尝试重新绑定同一个目录。
    _drop("乙的后续素材.mp4", party["library"])
    _scan(party["token_b"])
    _bind(party["token_b"], party["library"])

    still_mine = client.get(
        f"/api/production/materials/{original['id']}", headers=_headers(party["token_a"])
    )
    assert still_mine.status_code == 200, still_mine.text
    denied_for_b = client.get(
        f"/api/production/materials/{original['id']}", headers=_headers(party["token_b"])
    )
    assert denied_for_b.status_code == 404, denied_for_b.text


def test_repeated_import_of_same_link_does_not_expose_private_details(tmp_path, monkeypatch):
    """同一链接重复导入：不得把甲私有素材的 id/路径/详情带给乙。"""
    party = _setup(tmp_path, monkeypatch)
    _bind(party["token_a"], party["library"])
    party["link_state"].handler = serve(tmp_png(), "image/png")

    first = client.post(
        "/api/production/materials/link/import",
        json={"url": IMAGE_URL},
        headers=_headers(party["token_a"]),
    )
    assert first.status_code == 200, first.text
    asset_id = first.json()["asset_id"]

    repeated = client.post(
        "/api/production/materials/link/import",
        json={"url": IMAGE_URL},
        headers=_headers(party["token_b"]),
    )

    if repeated.status_code == 200:
        # 若服务端选择为乙建独立对象，不得回传甲的私有标识。
        body = repeated.json()
        assert body["asset_id"] != asset_id
        assert body.get("file_path") != first.json().get("file_path")
    else:
        # 沿既有拒绝路径（403/404/409）同样满足：没有回传甲的私有标识即可。
        assert repeated.status_code in (403, 404, 409)

    # 无论哪种结果，乙都读不到甲的私有素材。
    denied = client.get(
        f"/api/production/materials/{asset_id}", headers=_headers(party["token_b"])
    )
    assert denied.status_code == 404, denied.text
    listed = client.get("/api/production/materials", headers=_headers(party["token_b"]))
    assert all(item["id"] != asset_id for item in listed.json()["assets"])


def serve(body: bytes, content_type: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": content_type, "content-length": str(len(body))},
        )

    return handler


def tmp_png() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (8, 6), (12, 90, 200)).save(stream, format="PNG")
    return stream.getvalue()


def test_app_import_confirmation_branch_keeps_the_confirming_member_identity(
    tmp_path, monkeypatch
):
    """应用内导入确认分支：确认人也必须以本次身份判定归属，不落回绑定者。"""
    party = _setup(tmp_path, monkeypatch)
    _bind(party["token_a"], party["library"])
    _drop("乙应用内导入.mp4", party["library"])

    status = client.get("/api/production/materials/intake", headers=_headers(party["token_b"]))
    assert status.status_code == 200, status.text
    pending = status.json()["pending_files"]
    assert pending, status.text
    for item in pending:
        confirmed = client.post(
            "/api/production/materials/intake/copy-complete",
            json={**item, "confirmation_source": "app_import"},
            headers=_headers(party["token_b"]),
        )
        assert confirmed.status_code == 200, confirmed.text

    scanned = client.post("/api/production/materials/scan", headers=_headers(party["token_b"]))
    assert scanned.status_code == 200, scanned.text

    # 与 manual 确认同一判据：确认后入库的素材归本次确认人，乙本人可读。
    listed = client.get("/api/production/materials", headers=_headers(party["token_b"]))
    assert listed.status_code == 200, listed.text
    names = [item["file_name"] for item in listed.json()["assets"]]
    assert "乙应用内导入.mp4" in names, listed.json()
