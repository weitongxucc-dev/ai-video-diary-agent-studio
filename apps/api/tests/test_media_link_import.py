"""链接导入首批范围的传输替身验证。

全部用例都用 httpx.MockTransport，不建立真实连接，也不起本地服务；
生产侧的私网拒绝不因测试放宽——解析器注入返回公网地址即可。
"""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

import httpx
import pytest

from app.media_link_import import (
    LinkProvenance,
    MediaLinkImportError,
    MediaLinkImportService,
    RegisteredLink,
    _probe_file_media_type,
)

PUBLIC = "93.184.216.34"


class RecordingSink:
    """只记录写入的替身；它不知道已登记了什么，所以查重一律当作没有。"""

    def __init__(self) -> None:
        self.records: list[LinkProvenance] = []
        self.registered: RegisteredLink | None = None

    def existing_link_import(self, url: str) -> RegisteredLink | None:
        return self.registered

    def record_link_import(self, record: LinkProvenance) -> str:
        self.records.append(record)
        return f"provenance-{len(self.records)}"


def make_service(handler, tmp_path: Path, *, sink=None, max_bytes: int = 4 * 1024**3, prober=None):
    def factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    return MediaLinkImportService(
        client_factory=factory,
        intake_root=tmp_path / "intake",
        provenance=sink,
        max_bytes=max_bytes,
        resolver=lambda host: [PUBLIC],
        # 这里的载荷不是真实容器；真实格式探测由 test_material_link_import_route.py
        # 用真实 Pillow/ffprobe 覆盖。
        prober=prober or (lambda _path: "video"),
    )


def media_response(payload: bytes, content_type: str = "video/mp4", **kwargs) -> httpx.Response:
    headers = {"content-type": content_type, "content-length": str(len(payload))}
    headers.update(kwargs.pop("headers", {}))
    return httpx.Response(200, content=payload, headers=headers, **kwargs)


def test_check_reports_media_type_and_size_without_downloading(tmp_path):
    body = b"x" * 2048
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return media_response(body)

    result = make_service(handler, tmp_path).check("https://media.example.com/clip.mp4")

    assert result.supported is True
    assert result.media_type == "video"
    assert result.size_bytes == 2048
    assert result.file_name and result.file_name.endswith(".mp4")
    assert result.file_name.startswith("link-")
    assert len(calls) == 1
    assert not (tmp_path / "intake").exists(), "提交前检查不得落盘"


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("ftp://media.example.com/clip.mp4", "unsupported_scheme"),
        ("https://user:pw@media.example.com/clip.mp4", "credentials_in_url"),
        ("https://youtube.com/watch?v=abc", "social_page"),
        # 授权范围是 http(s)，两者都要能走通。
        ("http://media.example.com/clip.mp4", None),
        ("https://cdn.example.com/clip.mp4?token=1", None),
    ],
)
def test_rejects_urls_outside_the_supported_range(tmp_path, url, code):
    def handler(request: httpx.Request) -> httpx.Response:
        return media_response(b"data")

    if code is None:
        result = make_service(handler, tmp_path).check(url)
        assert result.supported is True
        return
    with pytest.raises(MediaLinkImportError) as excinfo:
        make_service(handler, tmp_path).check(url)
    assert excinfo.value.code == code


def test_rejects_private_and_loopback_sources(tmp_path):
    service = MediaLinkImportService(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(lambda r: media_response(b"x"))),
        intake_root=tmp_path / "intake",
        resolver=lambda host: ["127.0.0.1"],
    )
    with pytest.raises(MediaLinkImportError) as excinfo:
        service.check("http://intranet.example.com/clip.mp4")
    assert excinfo.value.code == "private_address"


def test_redirect_target_is_refused_before_the_request_is_sent(tmp_path):
    """跳转目标必须在访问前判定：不能先请求再校验。"""
    requested: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        if request.headers["host"] == "media.example.com":
            return httpx.Response(
                302, headers={"location": "http://intranet.example.com/clip.mp4"}
            )
        return media_response(b"should never be fetched")

    service = MediaLinkImportService(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        intake_root=tmp_path / "intake",
        resolver=lambda host: ["127.0.0.1"] if "intranet" in host else [PUBLIC],
    )

    with pytest.raises(MediaLinkImportError) as excinfo:
        service.check("https://media.example.com/clip.mp4")

    assert excinfo.value.code == "private_address"
    assert [request.headers["host"] for request in requested] == ["media.example.com"], (
        "私网跳转目标不得被访问过"
    )


def test_the_request_goes_to_the_validated_address_not_to_the_host_name(tmp_path):
    """只判定解析结果不够：请求必须发往已判定的公开地址，DNS 换址绕不回去。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return media_response(b"x" * 32)

    make_service(handler, tmp_path).check("https://media.example.com/clip.mp4")

    request = seen[0]
    assert request.url.host == PUBLIC, "连接目标必须是已判定的公开地址"
    assert request.headers["host"] == "media.example.com", "来源看到的仍是原主机名"
    assert request.extensions["sni_hostname"] == "media.example.com", "TLS 仍按原主机名校验证书"


def test_a_compressed_response_is_not_mistaken_for_a_truncated_one(tmp_path):
    """Content-Length 描述压缩后的长度，不能拿来判断我们实际写盘字节是否完整。"""
    body = b"media-bytes" * 256
    compressed = gzip.compress(body)
    sink = RecordingSink()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=compressed,
            headers={
                "content-type": "video/mp4",
                "content-encoding": "gzip",
                "content-length": str(len(compressed)),
            },
        )

    result = make_service(handler, tmp_path, sink=sink).import_link(
        "https://media.example.com/clip.mp4"
    )

    assert Path(result.file_path).read_bytes() == body
    assert result.size_bytes == len(body)
    assert result.file_path.endswith(".mp4")


def test_follows_a_public_redirect_to_the_media_file(tmp_path):
    body = b"redirected" * 32

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://media.example.com/clip.mp4":
            return httpx.Response(302, headers={"location": "https://cdn.example.com/real.mp4"})
        return media_response(body)

    result = make_service(handler, tmp_path).check("https://media.example.com/clip.mp4")
    assert result.supported is True
    assert result.size_bytes == len(body)


def test_redirect_loop_stops_instead_of_following_forever(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://media.example.com/clip.mp4"})

    with pytest.raises(MediaLinkImportError) as excinfo:
        make_service(handler, tmp_path).check("https://media.example.com/clip.mp4")
    assert excinfo.value.code == "too_many_redirects"


def test_rejects_web_pages_login_pages_and_placeholders(tmp_path):
    cases = [
        (httpx.Response(200, text="<html>player</html>", headers={"content-type": "text/html; charset=utf-8"}), "web_page_not_media"),
        (httpx.Response(403), "credentials_required"),
        (httpx.Response(404), "not_found"),
        (httpx.Response(200, content=b"{}", headers={"content-type": "application/json"}), "unsupported_media_type"),
    ]
    for response, code in cases:
        def handler(request: httpx.Request, response=response) -> httpx.Response:
            return httpx.Response(
                response.status_code,
                content=response.content,
                headers=response.headers,
            )

        with pytest.raises(MediaLinkImportError) as excinfo:
            make_service(handler, tmp_path).check("https://media.example.com/clip.mp4")
        assert excinfo.value.code == code


def test_import_downloads_and_records_provenance(tmp_path):
    body = b"video-bytes" * 512
    sink = RecordingSink()
    service = make_service(lambda r: media_response(body), tmp_path, sink=sink)

    result = service.import_link("https://media.example.com/clip.mp4")

    assert Path(result.file_path).read_bytes() == body
    assert result.size_bytes == len(body)
    assert result.media_type == "video"
    assert result.provenance_id == "provenance-1"
    assert result.reused_existing_file is False
    assert not list((tmp_path / "intake").glob("*.part"))
    record = sink.records[0]
    assert record.url == "https://media.example.com/clip.mp4"
    assert record.file_path == result.file_path
    assert record.size_bytes == len(body)


def test_same_url_reuses_the_existing_file_and_record(tmp_path):
    body = b"same-bytes" * 256
    sink = RecordingSink()
    service = make_service(lambda r: media_response(body), tmp_path, sink=sink)

    first = service.import_link("https://media.example.com/clip.mp4")
    second = service.import_link("https://media.example.com/clip.mp4")

    assert second.file_path == first.file_path
    assert second.reused_existing_file is True
    assert second.size_bytes == first.size_bytes
    assert len(sink.records) == 2, "替身不知道已登记什么，两次提交都照常登记"
    files = [p.name for p in (tmp_path / "intake").iterdir()]
    assert files == [first.file_name]


def test_a_registered_link_is_reused_before_anything_is_downloaded(tmp_path):
    """同一原始网址已登记且原文件完整可读时，连下载都不发生。

    来源这次给的内容长度不同，如果先下载再问登记，partial.replace 会把原文件覆盖掉。
    """
    sink = RecordingSink()
    sink.registered = RegisteredLink(
        url="https://media.example.com/clip.mp4",
        file_path=str(tmp_path / "link-original.mp4"),
        file_name="link-original.mp4",
        media_type="video",
        size_bytes=1234,
        asset_id="asset-original",
    )
    downloaded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        downloaded.append(request)
        return media_response(b"y" * 4096)

    result = make_service(handler, tmp_path, sink=sink).import_link(
        "https://media.example.com/clip.mp4"
    )

    assert len(downloaded) == 1, "只允许提交前检查那一次请求"
    assert result.asset_id == "asset-original"
    assert result.file_path == sink.registered.file_path
    assert result.size_bytes == 1234
    assert result.reused_existing_file is True
    assert sink.records == [], "没有新文件就不能再登记一次出处"


def test_import_refuses_to_report_completion_without_provenance(tmp_path):
    service = make_service(lambda r: media_response(b"bytes" * 32), tmp_path)

    with pytest.raises(MediaLinkImportError) as excinfo:
        service.import_link("https://media.example.com/clip.mp4")

    assert excinfo.value.code == "provenance_unavailable"
    assert "未完成" in excinfo.value.message


def test_incomplete_and_empty_downloads_leave_nothing_registered(tmp_path):
    body = b"y" * 100

    def short_handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, content=body, headers={"content-type": "video/mp4"})
        return httpx.Response(
            200,
            content=body[:40],
            headers={"content-type": "video/mp4", "content-length": str(len(body))},
        )

    sink = RecordingSink()
    with pytest.raises(MediaLinkImportError) as excinfo:
        make_service(short_handler, tmp_path, sink=sink).import_link("https://media.example.com/clip.mp4")
    assert excinfo.value.code == "incomplete_download"
    assert sink.records == []
    assert list((tmp_path / "intake").iterdir()) == []

    def empty_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-type": "video/mp4"})

    with pytest.raises(MediaLinkImportError) as excinfo:
        make_service(empty_handler, tmp_path, sink=sink).import_link("https://media.example.com/empty.mp4")
    assert excinfo.value.code == "empty_file"
    assert sink.records == []


def test_a_file_no_local_tool_can_read_is_never_registered(tmp_path):
    """真实格式读不出来时不得登记，扩展名和 Content-Type 都不算数。"""
    sink = RecordingSink()
    service = make_service(
        lambda r: media_response(b"x" * 64), tmp_path, sink=sink, prober=lambda _path: None
    )

    with pytest.raises(MediaLinkImportError) as excinfo:
        service.import_link("https://media.example.com/clip.mp4")

    assert excinfo.value.code == "unreadable_format"
    assert list((tmp_path / "intake").iterdir()) == []
    assert sink.records == []


def test_the_real_format_wins_over_the_declared_one(tmp_path):
    sink = RecordingSink()
    service = make_service(
        lambda r: media_response(b"x" * 64), tmp_path, sink=sink, prober=lambda _path: "image"
    )

    result = service.import_link("https://media.example.com/clip.mp4")

    assert result.media_type == "image"
    assert result.file_name.endswith(".jpg")
    assert sink.records[0].media_type == "image"


def test_an_embedded_cover_picture_does_not_make_audio_look_like_video(tmp_path):
    """正常 MP3 带封面图：封面是美术，不是这个文件真正的媒体类型。"""
    sample = Path("/tmp/wil102-ui-preparation/media/audio-cover.mp3")
    if not sample.is_file():
        pytest.skip("现成的封面音频样本不在本机")

    assert _probe_file_media_type(sample) == "audio"


def test_download_stops_at_the_size_limit(tmp_path):
    body = b"z" * 4096
    sink = RecordingSink()
    service = make_service(
        lambda r: media_response(body), tmp_path, sink=sink, max_bytes=1024
    )

    with pytest.raises(MediaLinkImportError) as excinfo:
        service.import_link("https://media.example.com/big.mp4")

    assert excinfo.value.code == "too_large"
    assert list((tmp_path / "intake").iterdir()) == []
    assert sink.records == []


@pytest.mark.parametrize(
    ("content_type", "url", "expected"),
    [
        ("audio/mpeg", "https://media.example.com/voice", "audio"),
        ("", "https://media.example.com/photo.png", "image"),
        ("application/octet-stream", "https://media.example.com/movie.mov", "video"),
    ],
)
def test_media_type_from_content_type_or_extension(tmp_path, content_type, url, expected):
    payload = b"media" * 32
    response = httpx.Response(
        200, content=payload, headers={"content-type": content_type or "application/octet-stream"}
    )
    if not content_type:
        response.headers.pop("content-type")
        response = httpx.Response(200, content=payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers=response.headers)

    result = make_service(handler, tmp_path).check(url)
    assert result.media_type == expected


def test_target_name_is_deterministic_per_url(tmp_path):
    from app.media_link_import import _file_name_for

    first = _file_name_for("https://media.example.com/a.mp4", ".mp4")
    again = _file_name_for("https://media.example.com/a.mp4", ".mp4")
    other = _file_name_for("https://media.example.com/b.mp4", ".mp4")

    assert first == again
    assert first != other
    assert hashlib.sha256(b"https://media.example.com/a.mp4").hexdigest()[:16] in first