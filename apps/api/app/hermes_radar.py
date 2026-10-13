from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.source_types import TopicSourceItem


@dataclass(frozen=True)
class HermesRadarStatus:
    configured: bool
    exists: bool
    path: str | None
    latest_report: str | None
    total_count: int
    product_hunt_count: int
    watcha_count: int


@dataclass
class _ReportEntry:
    source_type: str
    title: str
    ref: str = ""
    excerpt_lines: list[str] | None = None

    def __post_init__(self) -> None:
        if self.excerpt_lines is None:
            self.excerpt_lines = []

    def to_source_item(self) -> TopicSourceItem | None:
        if not self.title or not self.ref:
            return None
        assert self.excerpt_lines is not None
        excerpt = "\n".join(line for line in self.excerpt_lines if line).strip()
        return TopicSourceItem(
            source_type=self.source_type,
            title=self.title,
            ref=self.ref,
            excerpt=excerpt,
        )


class HermesRadarSourceClient:
    def __init__(self, report_dir: str | Path | None, limit: int = 80) -> None:
        self.report_dir = Path(report_dir).expanduser() if report_dir else None
        self._limit = limit

    def fetch_items(self, report_date: date | None = None) -> list[TopicSourceItem]:
        report = self._report_path(report_date)
        if report is None or not report.exists() or not report.is_file():
            return []
        text = report.read_text(encoding="utf-8", errors="ignore")
        return self._parse_report(text)[: self._limit]

    def latest_report_name(self) -> str | None:
        report = self._report_path(None)
        return report.name if report else None

    def status(self) -> HermesRadarStatus:
        items = self.fetch_items()
        return HermesRadarStatus(
            configured=self.report_dir is not None,
            exists=bool(self.report_dir and self.report_dir.exists() and self.report_dir.is_dir()),
            path=str(self.report_dir) if self.report_dir else None,
            latest_report=self.latest_report_name(),
            total_count=len(items),
            product_hunt_count=len([item for item in items if item.source_type == "Product Hunt"]),
            watcha_count=len([item for item in items if item.source_type == "观猹"]),
        )

    def _report_path(self, report_date: date | None) -> Path | None:
        if self.report_dir is None:
            return None
        if report_date is not None:
            return self.report_dir / f"{report_date.isoformat()}.md"
        if not self.report_dir.exists() or not self.report_dir.is_dir():
            return None
        reports = sorted(self.report_dir.glob("*.md"), key=lambda path: path.name, reverse=True)
        return reports[0] if reports else None

    def _parse_report(self, text: str) -> list[TopicSourceItem]:
        items: list[TopicSourceItem] = []
        current_section: str | None = None
        current_entry: _ReportEntry | None = None

        def flush_entry() -> None:
            nonlocal current_entry
            if current_entry is None:
                return
            item = current_entry.to_source_item()
            if item:
                items.append(item)
            current_entry = None

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if "Product Hunt 今日热门" in line:
                flush_entry()
                current_section = "Product Hunt"
                continue
            if "观猹 产品库精选" in line or "观猹产品库精选" in line:
                flush_entry()
                current_section = "观猹"
                continue
            if line == "---":
                flush_entry()
                continue
            if current_section is None:
                continue

            entry_match = re.match(r"^\*\*(\d+)\.\s*(.+?)\*\*$", line)
            if entry_match:
                flush_entry()
                title, ref = self._parse_entry_title(entry_match.group(2).strip())
                current_entry = _ReportEntry(
                    source_type=current_section,
                    title=title,
                    ref=ref,
                )
                continue
            if current_entry is None:
                continue
            if line.startswith("🔗"):
                current_entry.ref = line.removeprefix("🔗").strip()
                continue
            if line.startswith("📝"):
                current_entry.excerpt_lines.append(line.removeprefix("📝").strip())
                continue
            if line.startswith("🇨🇳"):
                current_entry.excerpt_lines.append(line.removeprefix("🇨🇳").strip())
                continue
            if line.startswith("*生成时间"):
                continue
            current_entry.excerpt_lines.append(line)

        flush_entry()
        return items

    def _parse_entry_title(self, raw_title: str) -> tuple[str, str]:
        link_match = re.match(r"^\[(?P<title>[^\]]+)\]\((?P<ref>[^)]+)\)$", raw_title)
        if link_match:
            return link_match.group("title").strip(), link_match.group("ref").strip()
        return raw_title, ""
