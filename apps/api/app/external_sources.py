from __future__ import annotations

import html
import ipaddress
import re
import socket
from urllib.parse import urlparse
from urllib.parse import urljoin

import httpx

from app.sources import TopicSourceItem


class ExternalSourceResponseError(RuntimeError):
    pass


class HtmlPageSourceClient:
    def __init__(
        self,
        urls: list[str] | tuple[str, ...],
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._urls = [url for url in urls if url.strip()]
        self._transport = transport
        self._timeout = timeout
        self._resolve_hostnames = transport is None

    def fetch_items(self) -> list[TopicSourceItem]:
        items: list[TopicSourceItem] = []
        with httpx.Client(
            follow_redirects=False,
            transport=self._transport,
            timeout=self._timeout,
        ) as client:
            for url in self._urls:
                try:
                    response = self._get_public_url(client, url)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise ExternalSourceResponseError(f"external source failed: {url}") from exc
                items.append(self._item_from_html(url, response.text))
        return items

    def _get_public_url(
        self,
        client: httpx.Client,
        url: str,
        *,
        max_redirects: int = 3,
    ) -> httpx.Response:
        current_url = url
        for _ in range(max_redirects + 1):
            self._raise_for_private_network_url(current_url)
            response = client.get(current_url)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location")
            if not location:
                return response
            current_url = urljoin(current_url, location)
        raise ExternalSourceResponseError(f"external source redirected too many times: {url}")

    def _raise_for_private_network_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ExternalSourceResponseError(f"unsupported external source url: {url}")

        hostname = parsed.hostname.strip().lower().rstrip(".")
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise ExternalSourceResponseError(f"external source targets private network: {url}")

        addresses = _literal_ip_addresses(hostname)
        if not addresses and self._resolve_hostnames:
            addresses = _resolved_ip_addresses(hostname)
        if any(_is_private_network_address(address) for address in addresses):
            raise ExternalSourceResponseError(f"external source targets private network: {url}")

    def _item_from_html(self, url: str, text: str) -> TopicSourceItem:
        title = self._title(text) or url
        excerpt = self._excerpt(text)
        return TopicSourceItem(
            source_type=self._source_type(url),
            title=title,
            ref=url,
            excerpt=excerpt,
        )

    def _source_type(self, url: str) -> str:
        host = urlparse(url).netloc.lower()
        if "watcha.cn" in host:
            return "观猹"
        return "外链"

    def _title(self, text: str) -> str | None:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
        if title_match:
            return self._clean_text(title_match.group(1))
        h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", text, flags=re.I | re.S)
        if h1_match:
            return self._clean_text(h1_match.group(1))
        return None

    def _excerpt(self, text: str, max_chars: int = 800) -> str:
        without_scripts = re.sub(
            r"<(script|style)[^>]*>.*?</\1>",
            " ",
            text,
            flags=re.I | re.S,
        )
        visible_text = re.sub(r"<[^>]+>", " ", without_scripts)
        return self._clean_text(visible_text)[:max_chars]

    def _clean_text(self, text: str) -> str:
        unescaped = html.unescape(text)
        return re.sub(r"\s+", " ", unescaped).strip()


def _literal_ip_addresses(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(hostname)]
    except ValueError:
        return []


def _resolved_ip_addresses(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ExternalSourceResponseError(f"external source host could not be resolved: {hostname}") from exc

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        raw_address = info[4][0]
        try:
            addresses.append(ipaddress.ip_address(raw_address))
        except ValueError:
            continue
    return addresses


def _is_private_network_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )
