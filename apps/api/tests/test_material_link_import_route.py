"""公开媒体直链导入的真实接口链路。

走真实 FastAPI 路由、真实 ProductionRepository（隔离的临时库与素材目录），
只把出网传输换成 httpx.MockTransport。格式探测走真实的 Pillow / ffprobe：
下载到的文件必须真的能被本地工具读出来，才允许登记。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app import main as _main_placeholder  # noqa: F401  (ensure app import happens first)
from app.media_link_import import MediaLinkImportService
from app.local_members import LocalMemberService
from app.production import ProductionRepository

PUBLIC = "93.184.216.34"
PNG_URL = "https://media.example.com/still.png"
AUDIO_URL = "https://media.example.com/voice.m4a"
VIDEO_URL = "https://media.example.com/clip.mp4"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def synthesize(path: Path, arguments: list[str]) -> bytes:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *arguments, str(path)],
        check=True,
        capture_output=True,
    )
    return path.read_bytes()


@pytest.fixture
def link_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    library = tmp_path / "library"
    library.mkdir()
    repository = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=library,
        duration_reader=lambda _path: 1.0,
        scene_reader=lambda _path, _duration: [],
    )
    repository.bind_material_ingestion("workspace-a", expected_directory=str(library))
    monkeypatch.setattr(main_module, "production_repo", repository)
    members = LocalMemberService(tmp_path / "members.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", members)
    _, token = members.whoami(None)
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(video_inbox_path=library, video_output_path=tmp_path / "out"),
    )
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace-a"),
    )

    state = SimpleNamespace(handler=None)

    def client_factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(state.handler), follow_redirects=False)

    # 只替换传输和解析器：绑定校验、出处登记和真实服务装配都走产品代码。
    def service_factory(**kwargs) -> MediaLinkImportService:
        return MediaLinkImportService(
            **{**kwargs, "client_factory": client_factory, "resolver": lambda _host: [PUBLIC]}
        )

    monkeypatch.setattr(main_module, "MediaLinkImportService", service_factory)
    return SimpleNamespace(
        client=TestClient(main_module.app, headers={"X-Local-Member-Token": token}),
        repository=repository,
        library=library,
        state=state,
    )


def serve(body: bytes, content_type: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body, headers={"content-type": content_type, "content-length": str(len(body))}
        )

    return handler


def tmp_png() -> bytes:
    from io import BytesIO

    stream = BytesIO()
    Image.new("RGB", (8, 6), (12, 90, 200)).save(stream, format="PNG")
    return stream.getvalue()


def test_link_reuse_and_restore_follow_current_visibility_without_transferring_owner(link_client) -> None:
    repo = link_client.repository
    source = link_client.library / "link-original.png"
    source.write_bytes(tmp_png())
    asset = repo.register_link_material(
        "workspace-a", source, media_type="image", source_url=PNG_URL,
        verified_owner_user_id="uploader",
    )
    with pytest.raises(KeyError):
        repo.existing_link_material("workspace-a", PNG_URL, viewer_user_id="other-member")
    shared = repo.set_asset_visibility("workspace-a", asset.id, "shared")
    assert repo.existing_link_material("workspace-a", PNG_URL, viewer_user_id="other-member").id == shared.id
    repo.set_asset_visibility("workspace-a", asset.id, "private")
    source.unlink()
    replacement = link_client.library / "link-replacement.png"
    replacement.write_bytes(tmp_png())
    with pytest.raises(KeyError):
        repo.register_link_material(
            "workspace-a", replacement, media_type="image", source_url=PNG_URL,
            verified_owner_user_id="other-member",
        )
    assert replacement.is_file()
    restored = repo.register_link_material(
        "workspace-a", replacement, media_type="image", source_url=PNG_URL,
        verified_owner_user_id="uploader",
    )
    assert restored.id == asset.id
    assert restored.owner_user_id == "uploader"
    assert restored.visibility == "private"


def test_image_link_registers_a_real_material_asset(link_client) -> None:
    """路由真实存在；图片下载后进入 material_assets 并带出处。"""
    body = tmp_png()
    link_client.state.handler = serve(body, "image/png")

    check = link_client.client.post(
        "/api/production/materials/link/check", json={"url": PNG_URL}
    )
    assert check.status_code == 200, check.text
    assert check.json()["media_type"] == "image"
    assert check.json()["supported"] is True
    assert not list(link_client.library.iterdir()), "提交前检查不得落盘"

    imported = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    )
    assert imported.status_code == 200, imported.text
    body_json = imported.json()
    assert body_json["media_type"] == "image"
    assert body_json["reused_existing_file"] is False
    assert body_json["asset_id"].startswith("asset_")

    asset = link_client.repository.get_asset("workspace-a", body_json["asset_id"])
    assert asset.media_type == "image"
    assert asset.source_url == PNG_URL
    assert asset.size_bytes == len(body)
    # 内容分析不属于本单：非视频必须如实显示未就绪，不得伪 ready。
    assert asset.analysis_status == "blocked"
    assert "内容分析尚未接入" in asset.analysis_message
    assert asset.segments == []


def test_the_same_source_url_reuses_one_material(link_client) -> None:
    link_client.state.handler = serve(tmp_png(), "image/png")

    first = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    ).json()
    second = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    ).json()

    assert first["asset_id"] == second["asset_id"]
    assert second["reused_existing_file"] is True
    assert [p.name for p in link_client.library.iterdir()] == [first["file_name"]]
    assert link_client.repository.list_assets_page("workspace-a", limit=24, offset=0)[1] == 1


def test_provenance_and_media_type_survive_a_reopen(link_client) -> None:
    link_client.state.handler = serve(tmp_png(), "image/png")
    imported = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    ).json()

    reopened = link_client.repository.get_asset("workspace-a", imported["asset_id"])
    assert reopened.source_url == PNG_URL
    assert reopened.media_type == "image"

    listed = link_client.client.get("/api/production/materials").json()
    row = next(item for item in listed["assets"] if item["id"] == imported["asset_id"])
    assert row["media_type"] == "image"
    assert row["source_url"] == PNG_URL

    media = link_client.client.get(f"/api/production/materials/{imported['asset_id']}/media")
    assert media.status_code == 200
    assert media.headers["content-type"] == "image/png"
    assert media.content == tmp_png()


def test_directory_scan_leaves_non_video_assets_alone(link_client) -> None:
    """接收白名单保持原样：非视频只由链接导入进入，不被扫描策略接管。"""
    link_client.state.handler = serve(tmp_png(), "image/png")
    link_client.client.post("/api/production/materials/link/import", json={"url": PNG_URL})

    discovered = link_client.repository.scan_library("workspace-a")

    assert discovered == [], "视频白名单不得因本单被改写"
    assert link_client.repository.get_asset(
        "workspace-a",
        link_client.repository.list_assets_page("workspace-a", limit=1, offset=0)[0][0].id,
    ).media_type == "image"


def test_a_page_or_a_non_media_file_is_never_registered(link_client) -> None:
    link_client.state.handler = lambda request: httpx.Response(
        200, text="<html>player</html>", headers={"content-type": "text/html; charset=utf-8"}
    )
    response = link_client.client.post(
        "/api/production/materials/link/import", json={"url": VIDEO_URL}
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "web_page_not_media"
    assert list(link_client.library.iterdir()) == []


def test_a_download_that_no_local_tool_can_read_is_not_registered(link_client) -> None:
    """扩展名和 Content-Type 都说是视频，真实格式却读不出来时不得登记。"""
    link_client.state.handler = serve(b"not a container at all", "video/mp4")
    response = link_client.client.post(
        "/api/production/materials/link/import", json={"url": VIDEO_URL}
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "unreadable_format"
    assert list(link_client.library.iterdir()) == []


def test_incomplete_download_registers_nothing(link_client) -> None:
    body = tmp_png()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body[:20],
            headers={"content-type": "image/png", "content-length": str(len(body))},
        )

    link_client.state.handler = handler
    response = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "incomplete_download"
    assert list(link_client.library.iterdir()) == []


def test_import_refuses_when_the_intake_is_not_bound_to_this_workspace(link_client) -> None:
    link_client.state.handler = serve(tmp_png(), "image/png")
    response = link_client.client.post(
        "/api/production/materials/link/import",
        json={"url": PNG_URL},
        headers={"x-test-workspace": "workspace-b"},
    )

    assert response.status_code == 409
    assert list(link_client.library.iterdir()) == []
    assert link_client.repository.list_assets_page("workspace-a", limit=24, offset=0)[1] == 0


def test_a_social_page_is_refused_before_any_request(link_client) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=tmp_png(), headers={"content-type": "image/png"})

    link_client.state.handler = handler
    response = link_client.client.post(
        "/api/production/materials/link/import",
        json={"url": "https://www.youtube.com/watch?v=abc"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "social_page"
    assert requested == []
    assert list(link_client.library.iterdir()) == []


def test_a_longer_body_for_the_same_png_url_never_overwrites_the_original(link_client) -> None:
    """同一 PNG 网址、同一扩展名、但这次内容更长：原文件与原记录都必须原样不动。

    落盘发生在登记之前，所以必须先查既有记录再决定要不要下载——否则
    partial.replace 会先把原文件盖掉，等到 sink 发现重复时已经来不及。
    """
    from io import BytesIO

    def png(width: int, height: int) -> bytes:
        stream = BytesIO()
        Image.new("RGB", (width, height), (200, 40, 40)).save(stream, format="PNG")
        return stream.getvalue()

    original = png(8, 6)
    link_client.state.handler = serve(original, "image/png")
    first = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    ).json()
    original_path = link_client.library / first["file_name"]
    original_bytes = original_path.read_bytes()
    assert original_bytes == original

    longer = png(120, 90)
    assert len(longer) != len(original), "两次响应长度必须不同"
    served: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        served.append(request)
        return httpx.Response(
            200,
            content=longer,
            headers={"content-type": "image/png", "content-length": str(len(longer))},
        )

    link_client.state.handler = handler
    second = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    )
    assert second.status_code == 200, second.text

    assert len(served) == 1, "只剩提交前检查那一次请求，不得再下载内容"
    assert second.json()["asset_id"] == first["asset_id"]
    assert second.json()["size_bytes"] == len(original)
    assert original_path.read_bytes() == original_bytes, "原文件内容不得被覆盖"
    assert link_client.repository.list_assets_page("workspace-a", limit=24, offset=0)[1] == 1
    recorded = link_client.repository.get_asset("workspace-a", first["asset_id"])
    assert recorded.size_bytes == len(original)
    assert recorded.source_url == PNG_URL


def test_a_same_length_garbage_file_is_never_reported_as_reusable(link_client) -> None:
    """原 PNG 被等长垃圾覆盖时不得声称"原文件完整可读"。

    大小相等不等于完整：只有真实格式还读得出来、且与登记类型一致，才允许复用。
    """
    original = tmp_png()
    link_client.state.handler = serve(original, "image/png")
    first = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    ).json()
    original_path = link_client.library / first["file_name"]
    assert original_path.read_bytes() == original

    original_path.write_bytes(b"\x00" * len(original))

    again = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    )
    assert again.status_code == 200, again.text

    assert again.json()["reused_existing_file"] is False, "垃圾文件不得被当作可复用"
    assert original_path.read_bytes() == original, "登记记录被恢复到可读的原始文件"
    assert link_client.repository.list_assets_page("workspace-a", limit=24, offset=0)[1] == 1
    assert link_client.repository.get_asset("workspace-a", first["asset_id"]).source_url == PNG_URL


def test_the_original_url_is_the_provenance_key_across_a_cdn_redirect(link_client) -> None:
    """出处与去重键是用户提交的原始地址；CDN 跳转地址只用来判断真实类型。"""
    body = tmp_png()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "media.example.com":
            return httpx.Response(
                302, headers={"location": "https://cdn.example.com/real.png?token=abc"}
            )
        return httpx.Response(
            200, content=body, headers={"content-type": "image/png", "content-length": str(len(body))}
        )

    link_client.state.handler = handler
    imported = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    )
    assert imported.status_code == 200, imported.text
    body_json = imported.json()
    assert body_json["url"] == PNG_URL
    assert body_json["media_type"] == "image"
    asset = link_client.repository.get_asset("workspace-a", body_json["asset_id"])
    assert asset.source_url == PNG_URL, "出处必须是用户提交的原始地址"
    # 再提交同一个原始地址，仍是同一条素材，不因 CDN 参数变化而重复登记。
    again = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    )
    assert again.status_code == 200, again.text
    assert again.json()["asset_id"] == body_json["asset_id"]
    assert link_client.repository.list_assets_page("workspace-a", limit=24, offset=0)[1] == 1


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe 不可用")
def test_one_original_url_never_becomes_two_materials(link_client) -> None:
    """重复提交不覆盖旧素材：原记录、原类型、原文件都不变，重复下载不留盘。

    本单没有"刷新/替换来源素材"的批准范围，同一 ID 的内容变了也不能让已有分析和
    任务引用失真，更不能把 mp4 字节写进旧的 .png 让媒体端点报错。
    """
    original = tmp_png()
    link_client.state.handler = serve(original, "image/png")
    first = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    ).json()
    assert first["media_type"] == "image"
    original_path = link_client.library / first["file_name"]
    assert original_path.read_bytes() == original

    video = synthesize(
        link_client.library.parent / "later.mp4",
        ["-f", "lavfi", "-i", "testsrc=size=32x32:rate=5:duration=0.4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p"],
    )
    link_client.state.handler = serve(video, "video/mp4")
    second = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    )
    assert second.status_code == 200, second.text

    assert second.json()["asset_id"] == first["asset_id"]
    assert second.json()["media_type"] == "image", "原类型不被后来的网页内容覆盖"
    assert second.json()["file_path"] == str(original_path)
    assert link_client.repository.list_assets_page("workspace-a", limit=24, offset=0)[1] == 1
    assert [p.name for p in link_client.library.iterdir()] == [first["file_name"]]
    assert original_path.read_bytes() == original, "原文件内容不得被覆盖"
    recorded = link_client.repository.get_asset("workspace-a", first["asset_id"])
    assert recorded.media_type == "image"
    assert recorded.source_url == PNG_URL


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe 不可用")
def test_a_missing_original_file_is_restored_onto_the_same_record(link_client) -> None:
    """原文件确实不在了，才按既有导入契约恢复；出处仍是同一个原始地址。"""
    link_client.state.handler = serve(tmp_png(), "image/png")
    first = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    ).json()
    (link_client.library / first["file_name"]).unlink()

    video = synthesize(
        link_client.library.parent / "later.mp4",
        ["-f", "lavfi", "-i", "testsrc=size=32x32:rate=5:duration=0.4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p"],
    )
    link_client.state.handler = serve(video, "video/mp4")
    second = link_client.client.post(
        "/api/production/materials/link/import", json={"url": PNG_URL}
    )
    assert second.status_code == 200, second.text

    assert second.json()["asset_id"] == first["asset_id"]
    assert second.json()["media_type"] == "video"
    assert link_client.repository.list_assets_page("workspace-a", limit=24, offset=0)[1] == 1
    restored = link_client.library / second.json()["file_name"]
    assert restored.is_file() and restored.read_bytes() == video
    recorded = link_client.repository.get_asset("workspace-a", first["asset_id"])
    assert recorded.source_url == PNG_URL


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe 不可用")
def test_audio_and_video_links_register_their_real_type(link_client) -> None:
    audio = synthesize(
        link_client.library.parent / "voice.m4a",
        ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.4", "-c:a", "aac"],
    )
    link_client.state.handler = serve(audio, "audio/mp4")
    audio_result = link_client.client.post(
        "/api/production/materials/link/import", json={"url": AUDIO_URL}
    )
    assert audio_result.status_code == 200, audio_result.text
    assert audio_result.json()["media_type"] == "audio"
    assert link_client.repository.get_asset(
        "workspace-a", audio_result.json()["asset_id"]
    ).media_type == "audio"

    video = synthesize(
        link_client.library.parent / "clip.mp4",
        ["-f", "lavfi", "-i", "testsrc=size=32x32:rate=5:duration=0.4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p"],
    )
    link_client.state.handler = serve(video, "video/mp4")
    video_result = link_client.client.post(
        "/api/production/materials/link/import", json={"url": VIDEO_URL}
    )
    assert video_result.status_code == 200, video_result.text
    assert video_result.json()["media_type"] == "video"
    registered = link_client.repository.get_asset("workspace-a", video_result.json()["asset_id"])
    assert registered.media_type == "video"
    assert registered.source_url == VIDEO_URL
    # 视频沿用既有分析流程：不伪 blocked，等扫描策略接手。
    assert registered.analysis_status == "pending"

    served = link_client.client.get(f"/api/production/materials/{registered.id}/media")
    assert served.headers["content-type"] == "video/mp4"
