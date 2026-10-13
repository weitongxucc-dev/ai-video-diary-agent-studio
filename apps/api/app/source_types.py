"""Source and knowledge value contracts, independent of storage and adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from app.knowledge_files import KnowledgeParameter, KnowledgeSection


ContentUseState = Literal[
    "available",
    "reference_only",
    "excluded",
    "not_for_automation",
    "not_for_publish",
]
ContentUsePurpose = Literal[
    "read", "reference", "understanding", "automatic_selection", "media", "preview", "publish"
]
CONTENT_USE_STATES = frozenset(
    {"available", "reference_only", "excluded", "not_for_automation", "not_for_publish"}
)


@dataclass(frozen=True)
class SourceConnector:
    id: str
    workspace_id: str
    platform: str
    protocol: str
    account_or_rule: str
    frequency: str
    permissions: tuple[str, ...]
    cursor: str | None = None
    connection_status: str = "configured"
    latest_run_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class SourceItem:
    id: str
    workspace_id: str
    connector_id: str
    source_label: str
    source_url: str | None
    content_type: str
    author: str | None
    published_at: datetime | None
    body: str | None
    subtitles: str | None
    media_references: tuple[str, ...]
    license: str
    title: str
    file_reference: str | None = None
    content_hash: str | None = None
    sections: tuple[KnowledgeSection, ...] = ()
    parameters: tuple[KnowledgeParameter, ...] = ()
    # 网页快照的抓取时间；其他来源没有抓取动作时为空。
    fetched_at: datetime | None = None
    use_state: ContentUseState = "available"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class KnowledgeItemVersion:
    knowledge_item_id: str
    version: str
    content_hash: str | None
    title: str
    body: str | None
    file_reference: str | None
    source_item_id: str | None
    sections: tuple[KnowledgeSection, ...]
    parameters: tuple[KnowledgeParameter, ...]
    saved_at: datetime
    # 这一版内容实际从哪个网址抓来、什么时候抓的。网页快照回查当时内容靠它。
    fetched_at: datetime | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class KnowledgeItem:
    id: str
    workspace_id: str
    content_type: str
    title: str
    body: str | None
    file_reference: str | None
    authorization: str
    ownership: str
    source_item_id: str | None
    version: str
    content_hash: str | None = None
    sections: tuple[KnowledgeSection, ...] = ()
    parameters: tuple[KnowledgeParameter, ...] = ()
    # 上传者身份（已验证的 user_id）。这是「私有内容只属于上传者」的判据；
    # 旧行或本地无身份写入留空，读取时按可见范围处理，不猜归属。
    owner_user_id: str | None = None
    # 当前版本内容的抓取时间；不是网页快照时为空。
    fetched_at: datetime | None = None
    source_url: str | None = None
    use_state: ContentUseState = "available"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class KnowledgeGrant:
    id: str
    knowledge_item_id: str
    source_workspace_id: str
    target_workspace_id: str
    granted_by: str
    granted_at: datetime
    revoked_by: str | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class SourceRunEvidence:
    id: str
    workspace_id: str
    connector_id: str
    status: str
    started_at: datetime
    finished_at: datetime
    cursor_before: str | None
    cursor_after: str | None
    item_count: int
    reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    retryable: bool | None = None


@dataclass(frozen=True)
class TopicSourceItem:
    source_type: str
    title: str
    ref: str
    excerpt: str
    source_url: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    license: str = "unknown"
    media_references: tuple[str, ...] = ()

    def to_source_item(self, workspace_id: str, connector_id: str) -> SourceItem:
        return SourceItem(
            id=f"src_{uuid4().hex}",
            workspace_id=workspace_id,
            connector_id=connector_id,
            source_label=self.source_type,
            source_url=self.source_url or self.ref,
            content_type="article",
            author=self.author,
            published_at=self.published_at,
            body=self.excerpt,
            subtitles=None,
            media_references=self.media_references,
            license=self.license,
            title=self.title,
        )


@dataclass(frozen=True)
class ObsidianNote:
    title: str
    ref: str
    excerpt: str
    modified_at: datetime
    tags: list[str]
    wikilinks: list[str]
    outlinks: list[str]

    def to_topic_source(self) -> TopicSourceItem:
        context_lines = [self.excerpt]
        if self.tags:
            context_lines.append(f"标签：{'、'.join(self.tags)}")
        if self.wikilinks:
            context_lines.append(f"双链：{'、'.join(self.wikilinks)}")
        if self.outlinks:
            context_lines.append(f"外链：{'、'.join(self.outlinks[:5])}")
        return TopicSourceItem(
            source_type="Obsidian",
            title=self.title,
            ref=self.ref,
            excerpt="\n".join(line for line in context_lines if line),
        )
