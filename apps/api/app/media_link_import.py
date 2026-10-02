"""链接导入首批范围：无需登录即可访问的公开媒体文件直链（PRD §7）。

只做三件事：提交前确认媒体类型与可接入结果；把文件完整下载到素材接收目录；
下载完整、文件非空并登记出处后才报告导入完成。同一网址重复提交落到同一个文件路径，
由现有接收流程复用既有记录，不产生重复素材。

登录页、需要凭据的来源和社交平台页面明确拒绝。不绕过访问限制，不把网页或占位文件
当媒体，也不为测试放宽生产边界——测试用传输替身，不建立真实连接。

两条信任边界是硬要求：每一跳都先判定解析结果再连接，并且请求直接发往已判定的公开
地址（保留原 Host 与 TLS SNI），否则 DNS 换址就能绕回私网；明确要求 identity 接收，
来源声明的长度只有在描述原始媒体字节时才用来判断下载完整性。
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Protocol
from urllib.parse import unquote, urlparse

import httpx
from PIL import Image, UnidentifiedImageError

MediaType = Literal["video", "audio", "image"]

# 服务端给出的类型优先；直链没有可靠 Content-Type 时才按扩展名判断。
CONTENT_TYPE_MEDIA: dict[str, MediaType] = {
    "video/mp4": "video",
    "video/quicktime": "video",
    "video/x-m4v": "video",
    "video/webm": "video",
    "audio/mpeg": "audio",
    "audio/mp4": "audio",
    "audio/aac": "audio",
    "audio/wav": "audio",
    "audio/x-wav": "audio",
    "audio/webm": "audio",
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "image/gif": "image",
}
EXTENSION_MEDIA: dict[str, MediaType] = {
    ".mp4": "video",
    ".mov": "video",
    ".m4v": "video",
    ".webm": "video",
    ".mp3": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".wav": "audio",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".webp": "image",
    ".gif": "image",
}
CONTENT_TYPE_EXTENSION: dict[str, str] = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-m4v": ".m4v",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# 这些站点的主页几乎都是播放页或登录页，直链需要平台私有解析，不在首批范围。
UNSUPPORTED_HOSTS = {
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
    "tiktok.com", "douyin.com", "iesdouyin.com", "kuaishou.com",
    "bilibili.com", "b23.tv", "xiaohongshu.com", "xhslink.com",
    "weibo.com", "weixin.qq.com", "twitter.com", "x.com",
    "instagram.com", "facebook.com", "fb.watch",
}

_DEFAULT_EXTENSION = {"video": ".mp4", "audio": ".m4a", "image": ".jpg"}

DEFAULT_MAX_BYTES = 4 * 1024**3
PROBE_TIMEOUT_SECONDS = 30
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class MediaLinkImportError(ValueError):
    """用户可见的拒绝原因。code 供接口层映射状态码，不在此层发 HTTP。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LinkCheck:
    url: str
    supported: bool
    media_type: MediaType | None
    file_name: str | None
    size_bytes: int | None
    message: str


@dataclass(frozen=True)
class LinkProvenance:
    url: str
    file_path: str
    file_name: str
    media_type: MediaType
    size_bytes: int


@dataclass(frozen=True)
class LinkImportResult:
    url: str
    media_type: MediaType
    file_name: str
    size_bytes: int
    file_path: str
    asset_id: str
    provenance_id: str
    reused_existing_file: bool


@dataclass(frozen=True)
class RegisteredLink:
    """同一原始网址已经登记、且原文件仍完整可读的那条素材。"""

    url: str
    file_path: str
    file_name: str
    media_type: MediaType
    size_bytes: int
    asset_id: str


class ProvenanceSink(Protocol):
    """出处登记由现有素材事实源承担；未接入时不得报告导入完成。"""

    def existing_link_import(self, url: str) -> RegisteredLink | None:
        """已在同一原始网址登记过、且原文件完整可读时返回它，否则返回 None。"""
        ...

    def record_link_import(self, record: LinkProvenance) -> str: ...


def _probe_file_media_type(path: Path) -> MediaType | None:
    """Read the real container type of a finished download.

    Reuses the same local tools the rest of the product already trusts — Pillow
    for still images, ffprobe for streams. No model, no network, no AI, no full
    decode. Returns None when the file is not a media container the product can
    actually read.
    """
    try:
        with Image.open(path) as image:
            image.verify()
        return "image"
    except UnidentifiedImageError:
        pass
    except (OSError, ValueError):
        # A file that looks like an image but does not decode is not a placeholder
        # we should fall through and try to read as a stream.
        return None
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-show_entries", "stream=codec_type,codec_name,width,height,sample_rate,channels",
                "-of", "json", str(path),
            ],
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        streams = json.loads(completed.stdout.decode("utf-8", "replace")).get("streams") or []
    except (json.JSONDecodeError, AttributeError):
        return None
    # codec_type alone is not evidence: ffprobe picks a demuxer from the file
    # extension, so a corrupt file named .png still reports a "video" stream
    # (codec_name png, width 0, height 0). A stream only counts when it names a
    # real codec and carries the dimensions or the sample layout it claims.
    kinds = {
        str(stream.get("codec_type"))
        for stream in streams
        if isinstance(stream, dict) and _stream_is_usable(stream)
    }
    if "video" in kinds:
        return "video"
    if "audio" in kinds:
        return "audio"
    return None


def _stream_is_usable(stream: dict) -> bool:
    if not stream.get("codec_name"):
        return False
    if stream.get("codec_type") == "video":
        return _as_int(stream.get("width")) > 0 and _as_int(stream.get("height")) > 0
    if stream.get("codec_type") == "audio":
        return _as_int(stream.get("sample_rate")) > 0 and _as_int(stream.get("channels")) > 0
    return False


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _resolve_host(host: str, resolver: Callable[[str], list[str]]) -> list[str]:
    try:
        return resolver(host)
    except OSError as exc:
        raise MediaLinkImportError("host_unresolved", f"无法解析来源地址：{host}") from exc


def _reject_private_address(addresses: list[str]) -> None:
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_reserved
            or parsed.is_multicast
            or parsed.is_unspecified
        ):
            raise MediaLinkImportError(
                "private_address", "该地址不是公开可访问的来源，不支持导入。"
            )


def _reject_credentials(parsed: object) -> None:
    if parsed.username or parsed.password:  # type: ignore[attr-defined]
        raise MediaLinkImportError(
            "credentials_in_url", "链接中带有账号或密码，请改用无需登录的公开直链。"
        )


def _normalized_url(raw: str) -> str:
    url = raw.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise MediaLinkImportError(
            "unsupported_scheme", "只支持 http(s) 直链；请确认来源地址可以公开访问。"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise MediaLinkImportError("invalid_url", "请填写完整的直链地址。")
    _reject_credentials(parsed)
    if host in UNSUPPORTED_HOSTS or any(
        host.endswith(f".{blocked}") for blocked in UNSUPPORTED_HOSTS
    ):
        raise MediaLinkImportError(
            "social_page", "这是社交平台页面，不是媒体文件直链；首批范围暂不支持。"
        )
    return url


@dataclass(frozen=True)
class GuardedHop:
    """一跳已判定过的访问：连到已核地址，仍以原主机名对外。"""

    public_url: str
    request_url: str
    host_header: str
    sni_hostname: str


def _guarded_hop(raw: str, resolver: Callable[[str], list[str]]) -> GuardedHop:
    """每次发起请求前都要过这一关：跳转目标同样先校验再访问。

    只判定解析结果还不够——判定之后交给 httpx，它会按主机名再解析一次，也可能走环境
    代理，DNS 换址就能绕回私网。所以请求直接发往已核公开地址，同时保留原 Host 与 TLS
    SNI：来源看到的和证书校验的都是真实主机名，没有关掉任何 TLS 校验。
    """
    url = _normalized_url(raw)
    parsed = urlparse(url)
    host = parsed.hostname or ""
    addresses = _resolve_host(host, resolver)
    _reject_private_address(addresses)
    return GuardedHop(
        public_url=url,
        request_url=str(httpx.URL(url).copy_with(host=addresses[0])),
        host_header=parsed.netloc,
        sni_hostname=host,
    )


def _media_type_for(url: str, content_type: str | None) -> MediaType | None:
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if declared in CONTENT_TYPE_MEDIA:
        return CONTENT_TYPE_MEDIA[declared]
    # 服务端给了明确的非媒体类型就不看扩展名，网页和占位文件不算媒体。
    if declared and declared != "application/octet-stream":
        return None
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    return EXTENSION_MEDIA.get(suffix)


def _extension_for(url: str, content_type: str | None, media_type: MediaType) -> str:
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if declared in CONTENT_TYPE_EXTENSION:
        return CONTENT_TYPE_EXTENSION[declared]
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    return suffix if EXTENSION_MEDIA.get(suffix) == media_type else _DEFAULT_EXTENSION[media_type]


def _reject_response(response: httpx.Response) -> None:
    if response.status_code in {401, 403}:
        raise MediaLinkImportError(
            "credentials_required", "该来源需要登录或凭据，首批范围暂不支持。"
        )
    if response.status_code == 404 or response.status_code == 410:
        raise MediaLinkImportError("not_found", "该直链不存在或已失效。")
    if response.status_code >= 400:
        raise MediaLinkImportError(
            "not_reachable", f"来源返回 {response.status_code}，暂时无法接入。"
        )


def _reject_web_page(response: httpx.Response, media_type: MediaType | None) -> None:
    declared = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if declared.startswith("text/"):
        raise MediaLinkImportError(
            "web_page_not_media", "这是网页地址，不是媒体文件直链。"
        )
    if media_type is None:
        raise MediaLinkImportError(
            "unsupported_media_type", "该来源不是可识别的视频、音频或图片文件。"
        )


def _file_name_for(url: str, extension: str) -> str:
    # 同一网址始终得到同一文件名，重复提交由现有接收流程复用同一条记录。
    return f"link-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}{extension}"


class MediaLinkImportService:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.Client],
        intake_root: Path,
        provenance: ProvenanceSink | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        resolver: Callable[[str], list[str]] | None = None,
        prober: Callable[[Path], MediaType | None] | None = None,
    ) -> None:
        # client_factory 每次必须返回新建的客户端，本服务负责关闭。
        self._client_factory = client_factory
        self._intake_root = Path(intake_root)
        self._provenance = provenance
        self._max_bytes = max_bytes
        self._resolver = resolver or _default_resolver
        self._prober = prober or _probe_file_media_type

    # 提交前：只读响应头，确认类型与可接入，不落盘。
    def check(self, url: str) -> LinkCheck:
        normalized, media_type, file_name, size = self._probe(url)
        label = {"video": "视频", "audio": "音频", "image": "图片"}[media_type]
        return LinkCheck(
            url=normalized,
            supported=True,
            media_type=media_type,
            file_name=file_name,
            size_bytes=size,
            message=f"可接入的{label}直链" + (f"，约 {_human_size(size)}" if size else "，来源未声明大小"),
        )

    # 下载 → 非空 → 完整 → 真实格式可读 → 落位 → 登记出处，全成才算导入完成。
    def import_link(self, url: str) -> LinkImportResult:
        if self._provenance is None:
            # 先确认出处可登记，再下载；否则白下一个文件却不能报告完成。
            raise MediaLinkImportError(
                "provenance_unavailable", "出处尚未登记，本次导入未完成，未下载任何文件。"
            )
        normalized, declared_type, file_name, advertised = self._probe(url)
        # 同一原始网址已经登记过、且原文件完整可读时直接复用。不先问清楚就下载，
        # 来源这次给的内容或长度一变，就会把原文件覆盖掉——到登记时已经来不及。
        # 刷新来源不在本单范围内，所以这里只复用，不更新也不重置分析。
        registered = self._provenance.existing_link_import(normalized)
        if registered is not None:
            return LinkImportResult(
                url=normalized,
                media_type=registered.media_type,
                file_name=registered.file_name,
                size_bytes=registered.size_bytes,
                file_path=registered.file_path,
                asset_id=registered.asset_id,
                provenance_id=registered.asset_id,
                reused_existing_file=True,
            )
        target = self._intake_root / file_name
        reused = False
        size_bytes = 0
        media_type = declared_type
        if advertised is not None and target.exists() and target.stat().st_size == advertised:
            # 同一网址重复提交：文件已在且完整，仍要确认真实格式再登记。
            existing_type = self._prober(target)
            if existing_type is not None:
                reused, size_bytes, media_type = True, advertised, existing_type
        if not reused:
            target, size_bytes, media_type = self._download(normalized, target, declared_type)
        asset_id = self._provenance.record_link_import(
            LinkProvenance(
                url=normalized,
                file_path=str(target),
                file_name=target.name,
                media_type=media_type,
                size_bytes=size_bytes,
            )
        )
        return LinkImportResult(
            url=normalized,
            media_type=media_type,
            file_name=target.name,
            size_bytes=size_bytes,
            file_path=str(target),
            asset_id=asset_id,
            provenance_id=asset_id,
            reused_existing_file=reused,
        )

    def _open(self) -> httpx.Client:
        try:
            return self._client_factory()
        except httpx.HTTPError as exc:
            raise MediaLinkImportError("not_reachable", f"无法连接来源：{exc}") from exc

    def _validated_get(self, client: httpx.Client, url: str) -> tuple[GuardedHop, httpx.Response]:
        """逐跳校验再访问：跳转目标在请求发出之前就过私网与范围检查。"""
        target = url
        for _ in range(MAX_REDIRECTS + 1):
            hop = _guarded_hop(target, self._resolver)
            response = client.send(
                client.build_request(
                    "GET",
                    hop.request_url,
                    headers={
                        "Accept": "*/*",
                        # 明确按原始媒体字节接收：压缩过的长度不能拿来判断完整性。
                        "Accept-Encoding": "identity",
                        "Host": hop.host_header,
                    },
                    extensions={"sni_hostname": hop.sni_hostname},
                ),
                stream=True,
                follow_redirects=False,
            )
            location = response.headers.get("location")
            if response.status_code in REDIRECT_STATUSES and location:
                response.close()
                target = str(httpx.URL(hop.public_url).join(location))
                continue
            return hop, response
        raise MediaLinkImportError("too_many_redirects", "跳转次数过多，未继续访问该来源。")

    def _probe(self, url: str) -> tuple[str, MediaType, str, int | None]:
        """只读响应头：确认可接入、媒体类型和目标文件名，不落盘。"""
        normalized = _normalized_url(url)
        hop = _guarded_hop(normalized, self._resolver)
        with self._open() as client:
            final, response = self._validated_get(client, hop.public_url)
            try:
                _reject_response(response)
                # 跳转到 CDN 后的地址只用来判断真实类型和扩展名；出处与去重键始终是
                # 用户最初提交的地址，否则跳转参数一变就会当成另一个来源重复登记。
                media_type = _media_type_for(final.public_url, response.headers.get("content-type"))
                _reject_web_page(response, media_type)
                extension = _extension_for(
                    final.public_url, response.headers.get("content-type"), media_type  # type: ignore[arg-type]
                )
                size = _advertised_size(response)
            finally:
                response.close()
        return normalized, media_type, _file_name_for(normalized, extension), size  # type: ignore[return-value]

    def _download(self, url: str, target: Path, declared_type: MediaType) -> tuple[Path, int, MediaType]:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f"{target.name}.part")
        written = 0
        try:
            with self._open() as client:
                hop, response = self._validated_get(client, url)
                try:
                    _reject_response(response)
                    media_type = _media_type_for(hop.public_url, response.headers.get("content-type"))
                    _reject_web_page(response, media_type)
                    advertised = _advertised_size(response)
                    if advertised is not None and advertised > self._max_bytes:
                        raise MediaLinkImportError(
                            "too_large", f"文件约 {_human_size(advertised)}，超过首批范围允许的大小。"
                        )
                    with partial.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            written += len(chunk)
                            if written > self._max_bytes:
                                raise MediaLinkImportError(
                                    "too_large", f"文件超过首批范围允许的大小 {_human_size(self._max_bytes)}。"
                                )
                            handle.write(chunk)
                        handle.flush()
                finally:
                    response.close()
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        if written == 0:
            partial.unlink(missing_ok=True)
            raise MediaLinkImportError("empty_file", "下载到的文件为空，未登记。")
        # 来源声明了大小时必须完整收到，否则不算导入完成。
        if advertised is not None and written != advertised:
            partial.unlink(missing_ok=True)
            raise MediaLinkImportError(
                "incomplete_download",
                f"下载不完整（{written} / {advertised} 字节），未登记。",
            )
        # 只有真实格式能被本地工具读出来才算媒体；扩展名和 Content-Type 都不算数。
        probed_type = self._prober(partial)
        if probed_type is None:
            partial.unlink(missing_ok=True)
            raise MediaLinkImportError(
                "unreadable_format", "下载到的文件不是可识别的视频、音频或图片，未登记。"
            )
        if probed_type != declared_type:
            # 以真实格式为准，文件名随之确定，保证同一网址始终落到同一路径。
            target = self._intake_root / _file_name_for(url, _DEFAULT_EXTENSION[probed_type])
        partial.replace(target)
        return target, written, probed_type


def _default_resolver(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def _advertised_size(response: httpx.Response) -> int | None:
    """来源声明的长度只在它是原始媒体字节数时才可用。

    我们明确要求 identity；若来源仍然压缩，Content-Length 描述的是压缩后的长度，
    与我们实际写盘的字节数本来就不相等，拿来判完整性会把合法文件误判成下载不完整。
    这种情况下宁可不做长度判断——真实格式探测仍然把关。
    """
    encoding = (response.headers.get("content-encoding") or "").strip().lower()
    if encoding and encoding != "identity":
        return None
    try:
        return int(response.headers["content-length"]) if "content-length" in response.headers else None
    except ValueError:
        return None


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"