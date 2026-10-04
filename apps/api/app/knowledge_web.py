"""网页快照：完整可引用正文、原网址、抓取时间和固定版本。

网页快照的出处定位只用页面自己真实存在的结构：正文按实际内容块收集，定位写成
所在标题和第几段。没有正文的页面（登录墙、空壳脚本页）明确失败，不截摘要凑数，
也不编造出处。

正文收集用标准库 HTMLParser 按真实嵌套解析：外层容器和内层内容块同时存在时只取
最内层内容块，所以 <article><h1>标题</h1><p>正文</p></article> 不会因为外层把
里面的标题和段落整块吃掉。
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from app.knowledge_files import KnowledgeSection

# 这些标签的内容不是文章正文：脚本、样式、导航、页脚等在阅读页里也不是正文。
_NON_CONTENT_TAGS = frozenset(
    {"script", "style", "noscript", "template", "svg", "nav", "header", "footer", "aside", "form"}
)
# 内容块：只从这些标签的最内层实例收集正文。
_BLOCK_TAGS = frozenset(
    {"p", "li", "blockquote", "pre", "dd", "dt", "figcaption", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"}
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# 容器标签本身不产正文，但它们里面的内容块要保留（不是「整块吃掉」）。
_CONTAINER_TAGS = frozenset({"div", "section", "article", "main", "body", "html", "tr", "table"})


class WebPageSnapshotError(ValueError):
    """网页没有可保存的正文时抛出，调用方必须显式失败。"""


@dataclass(frozen=True)
class WebPageSnapshot:
    """一次抓取得到的网页内容快照。"""

    url: str
    title: str
    full_text: str
    content_hash: str
    sections: tuple[KnowledgeSection, ...] = field(default_factory=tuple)


def snapshot_web_page(url: str, document: str) -> WebPageSnapshot:
    """把一份 HTML 响应解析成可引用的完整正文快照。"""
    if not document.strip():
        raise WebPageSnapshotError(f"网页没有返回可保存的内容：{url}")
    collector = _ContentCollector()
    collector.feed(document)
    collector.close()
    blocks = collector.blocks
    if not blocks:
        # 只有脚本和导航的页面没有正文可引用，摘要不能冒充完整内容。
        raise WebPageSnapshotError(f"网页没有可引用的正文内容：{url}")
    title = collector.document_title or next(
        (text for tag, text in blocks if tag in _HEADING_TAGS), url
    )
    sections = _sections_from_blocks(blocks)
    full_text = "\n\n".join(section.text for section in sections)
    clean_title = _clean_text(title)
    # 指纹包含标题和小节标题：只改标题或小节标题也是真实的页面变化，不能因为段落
    # 正文一样就当成同一版。抓取时间不进指纹，所以重复抓取同一页不添新版本。
    fingerprint = "\n".join(
        [clean_title, *(section.title for section in sections), full_text]
    )
    return WebPageSnapshot(
        url=url,
        title=clean_title,
        full_text=full_text,
        content_hash=hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(),
        sections=tuple(sections),
    )


class _ContentCollector(HTMLParser):
    """按真实嵌套收集正文块。

    只有最内层的内容块产出正文：外层 <article>/<div> 只是容器，遇到内层 <p> 时
    外层不再独立成块，所以标题和段落不会被容器整块替换掉。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self.document_title: str | None = None
        self._skip_depth = 0
        self._open_blocks: list[tuple[str, list[str]]] = []
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if self._skip_depth:
            if name in _NON_CONTENT_TAGS:
                self._skip_depth += 1
            return
        if name in _NON_CONTENT_TAGS:
            self._skip_depth = 1
            return
        if name == "title":
            self._in_title = True
            return
        # 普通 <br> 和自闭合 <br/> 一样：只作分隔，不丢字也不虚构成新段落。
        if name == "br" and self._open_blocks:
            self._open_blocks[-1][1].append(" ")
            return
        if name in _BLOCK_TAGS or name in _CONTAINER_TAGS:
            # 容器自己直接写的文字也是正文；它出现在下一个内层块之前，先结算出来，
            # 这样 <div>正文<br>后文<p>内层</p></div> 里容器那段不会被丢掉，
            # 也不会和内层 <p> 重复。
            if self._open_blocks and self._open_blocks[-1][1]:
                self._flush_open()
            self._open_blocks.append((name, []))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <br/> 是换行：不丢字，也不把它变成新的段落。行内文字合成同一段。
        if tag.lower() == "br" and self._open_blocks:
            self._open_blocks[-1][1].append(" ")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if self._skip_depth:
            if name in _NON_CONTENT_TAGS:
                self._skip_depth -= 1
            return
        if name == "title" and self._in_title:
            self._in_title = False
            self.document_title = _clean_text(" ".join(self._title_parts))
            return
        if not self._open_blocks:
            return
        # 只在最内层开着的那个元素闭合时结算它，避免容器提前吃掉内层内容。
        if name in _BLOCK_TAGS or name in _CONTAINER_TAGS:
            self._flush_open()

    def _flush_open(self) -> None:
        tag, parts = self._open_blocks.pop()
        # 行内片段（<strong>/<em> 等）直接相连，不当成不同段落。
        text = _clean_text("".join(parts))
        if text:
            # 容器直接写的正文和内容块正文同属正文，出处定位不区分这两种来源；
            # 标题保留自己的标签，用来给后面的段落标出所在小节。
            self.blocks.append((tag if tag in _HEADING_TAGS else "p", text))

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._skip_depth or not self._open_blocks:
            return
        # 保留原始空白：行内片段之间的空格是正文的一部分，不能 strip 掉。
        if data:
            self._open_blocks[-1][1].append(data)


def _sections_from_blocks(blocks: list[tuple[str, str]]) -> list[KnowledgeSection]:
    """按标题层级分段；每个内容块是一段，定位写出所在标题和段序。"""
    sections: list[KnowledgeSection] = []
    heading = ""
    for tag, text in blocks:
        if tag in _HEADING_TAGS:
            heading = text
            continue
        sections.append(KnowledgeSection(locator="", title=heading, text=text))
    return [
        KnowledgeSection(
            locator=f"「{section.title}」第 {index} 段" if section.title else f"第 {index} 段",
            title=section.title,
            text=section.text,
        )
        for index, section in enumerate(sections, start=1)
    ]


def _clean_text(text: str) -> str:
    return re.sub(r"[ \t ]+", " ", html.unescape(text)).strip()