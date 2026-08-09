from __future__ import annotations

import html
import re
from urllib.parse import urlparse

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

    def fetch_items(self) -> list[TopicSourceItem]:
        items: list[TopicSourceItem] = []
        with httpx.Client(
            follow_redirects=True,
            transport=self._transport,
            timeout=self._timeout,
        ) as client:
            for url in self._urls:
                try:
                    response = client.get(url)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise ExternalSourceResponseError(f"external source failed: {url}") from exc
                items.append(self._item_from_html(url, response.text))
        return items

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
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
        if title_match:
            return self._clean_text(title_match.group(1))
        h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", text, flags=re.IGNORECASE | re.DOTALL)
        if h1_match:
            return self._clean_text(h1_match.group(1))
        return None

    def _excerpt(self, text: str, max_chars: int = 800) -> str:
        without_scripts = re.sub(
            r"<(script|style)[^>]*>.*?</\1>",
            " ",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        visible_text = re.sub(r"<[^>]+>", " ", without_scripts)
        return self._clean_text(visible_text)[:max_chars]

    def _clean_text(self, text: str) -> str:
        unescaped = html.unescape(text)
        return re.sub(r"\s+", " ", unescaped).strip()
