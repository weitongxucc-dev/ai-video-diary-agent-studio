from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from app.knowledge_files import KnowledgeParameter, KnowledgeSection

if TYPE_CHECKING:
    from app.source_repository import SourceRepository


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
    # 当前版本内容的抓取时间；不是网页快照时为空。
    fetched_at: datetime | None = None
    source_url: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


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


class SourceBoundaryError(RuntimeError):
    """A platform boundary failure with a UI/API-safe, actionable semantic."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        impact: str | None = None,
        repair_action: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.impact = impact or "No new items were imported; existing items are unchanged."
        self.repair_action = repair_action or "Review the source configuration before retrying."
        self.retryable = retryable


class SourceSyncFailedError(RuntimeError):
    def __init__(self, run: SourceRunEvidence) -> None:
        super().__init__(run.reason or "source sync failed")
        self.run = run


def source_error_from_http_status(status_code: int, source_name: str) -> SourceBoundaryError:
    if status_code == 401:
        return SourceBoundaryError(
            "authentication_required", f"{source_name} requires login or a valid token",
            repair_action="Sign in again or update the source credential.", retryable=False,
        )
    if status_code == 403:
        return SourceBoundaryError(
            "access_denied", f"{source_name} denied access for the configured account or rule",
            repair_action="Confirm the account has permission for this source.", retryable=False,
        )
    if status_code == 429:
        return SourceBoundaryError(
            "rate_limited", f"{source_name} rate limit was reached",
            repair_action="Wait for the platform limit window, then retry.", retryable=True,
        )
    return SourceBoundaryError(
        "temporary_failure", f"{source_name} request failed with HTTP {status_code}",
        repair_action="Check the source service and retry.", retryable=True,
    )


def sync_source_connector(
    repository: SourceRepository,
    workspace_id: str,
    connector_id: str,
    fetch: Callable[[str | None], tuple[list[SourceItem], str | None]],
) -> SourceRunEvidence:
    """Persist an import attempt and force callers to observe failures."""
    connector = repository.get_connector(workspace_id, connector_id)
    try:
        items, cursor_after = fetch(connector.cursor)
        return repository.record_sync_success(
            workspace_id, connector_id, items, cursor_after
        )
    except SourceBoundaryError as exc:
        run = repository.record_sync_failure(
            workspace_id,
            connector_id,
            reason=str(exc),
            impact=exc.impact,
            repair_action=exc.repair_action,
            retryable=exc.retryable,
            connection_status=exc.kind,
        )
        raise SourceSyncFailedError(run) from exc
    except Exception as exc:
        boundary_error = SourceBoundaryError(
            "temporary_failure",
            f"{connector.platform} sync failed: {exc}",
            repair_action="Check the connector configuration and retry.",
            retryable=True,
        )
        run = repository.record_sync_failure(
            workspace_id,
            connector_id,
            reason=str(boundary_error),
            impact=boundary_error.impact,
            repair_action=boundary_error.repair_action,
            retryable=boundary_error.retryable,
            connection_status=boundary_error.kind,
        )
        raise SourceSyncFailedError(run) from exc


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


class ObsidianVault:
    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path).expanduser() if path else None

    @property
    def configured(self) -> bool:
        return self.path is not None

    @property
    def exists(self) -> bool:
        return bool(self.path and self.path.exists() and self.path.is_dir())

    def markdown_count(self) -> int:
        return len(self._markdown_files())

    def recent_notes(self, limit: int = 20) -> list[ObsidianNote]:
        files = sorted(
            self._markdown_files(),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [self._note_from_file(path) for path in files[:limit]]

    def _markdown_files(self) -> list[Path]:
        if not self.exists or self.path is None:
            return []
        return [
            path
            for path in self.path.rglob("*.md")
            if not any(part.startswith(".") for part in path.relative_to(self.path).parts)
        ]

    def _note_from_file(self, path: Path) -> ObsidianNote:
        assert self.path is not None
        text = path.read_text(encoding="utf-8", errors="ignore")
        ref = path.relative_to(self.path).as_posix()
        return ObsidianNote(
            title=self._title_from_text(text, path),
            ref=ref,
            excerpt=self._excerpt_from_text(text),
            modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
            tags=self._tags_from_text(text),
            wikilinks=self._wikilinks_from_text(text),
            outlinks=self._outlinks_from_text(text),
        )

    def _title_from_text(self, text: str, path: Path) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
        return path.stem

    def _excerpt_from_text(self, text: str, max_chars: int = 600) -> str:
        lines = []
        in_frontmatter = False
        for index, line in enumerate(text.splitlines()):
            stripped = line.strip()
            if index == 0 and stripped == "---":
                in_frontmatter = True
                continue
            if in_frontmatter:
                if stripped == "---":
                    in_frontmatter = False
                continue
            if stripped.startswith("#"):
                continue
            if stripped:
                lines.append(stripped)
        return "\n".join(lines)[:max_chars]

    def _tags_from_text(self, text: str) -> list[str]:
        tags: list[str] = []
        lines = text.splitlines()
        in_frontmatter = False
        in_tag_list = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if index == 0 and stripped == "---":
                in_frontmatter = True
                continue
            if in_frontmatter and stripped == "---":
                in_frontmatter = False
                in_tag_list = False
                continue
            if in_frontmatter:
                if stripped.startswith("tags:"):
                    raw_value = stripped.removeprefix("tags:").strip()
                    in_tag_list = True
                    tags.extend(self._split_tag_value(raw_value))
                    continue
                if in_tag_list and stripped.startswith("-"):
                    tags.extend(self._split_tag_value(stripped.removeprefix("-").strip()))
                    continue
                if stripped and not line.startswith(" "):
                    in_tag_list = False

        tags.extend(
            match.group(1).strip()
            for match in re.finditer(r"(?<![\w/])#([\w\u4e00-\u9fff][\w\u4e00-\u9fff/-]*)", text)
        )
        return self._unique_clean(tags)

    def _split_tag_value(self, value: str) -> list[str]:
        if not value:
            return []
        normalized = value.strip().strip("[]")
        return [
            item.strip().strip("'\"")
            for item in re.split(r"[,，]", normalized)
            if item.strip().strip("'\"")
        ]

    def _wikilinks_from_text(self, text: str) -> list[str]:
        return self._unique_clean(
            match.group(1).split("|", 1)[0].strip()
            for match in re.finditer(r"\[\[([^\]]+)\]\]", text)
        )

    def _outlinks_from_text(self, text: str) -> list[str]:
        markdown_urls = [
            match.group(1).strip()
            for match in re.finditer(r"\[[^\]]+\]\((https?://[^)\s]+)\)", text)
        ]
        bare_urls = [
            match.group(1).rstrip("。。，,)")  # noqa: B005
            for match in re.finditer(r"(?<!\()(?<!\]\()((?:https?://)[^\s<>\]]+)", text)
        ]
        return self._unique_clean([*markdown_urls, *bare_urls])

    def _unique_clean(self, values) -> list[str]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for value in values:
            item = str(value).strip()
            if not item or item in seen:
                continue
            seen.add(item)
            cleaned.append(item)
        return cleaned
