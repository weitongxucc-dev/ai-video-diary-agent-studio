from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class TopicSourceItem:
    source_type: str
    title: str
    ref: str
    excerpt: str


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
