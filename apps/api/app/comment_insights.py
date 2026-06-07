from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommentInsight:
    kind: str
    content: str
    source_comments: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        return round(min(1.0, 0.4 + len(self.source_comments) * 0.1), 2)


@dataclass(frozen=True)
class _InsightRule:
    kind: str
    content: str
    required_terms: tuple[str, ...]
    any_terms: tuple[str, ...] = ()

    def matches(self, comment: str) -> bool:
        if not all(term in comment for term in self.required_terms):
            return False
        return not self.any_terms or any(term in comment for term in self.any_terms)


_RULES = (
    _InsightRule(
        kind="prefer",
        content="喜欢真实经历开头",
        required_terms=("开头",),
        any_terms=("真实", "经历", "故事", "亲身"),
    ),
    _InsightRule(
        kind="prefer",
        content="教程类视频要步骤清楚",
        required_terms=(),
        any_terms=("步骤很清楚", "步骤清楚", "讲得清楚", "很清楚", "教程清楚"),
    ),
    _InsightRule(
        kind="prefer",
        content="工具测评要先讲实际体验",
        required_terms=(),
        any_terms=("实测结论", "先讲结论", "实际体验", "先说体验", "测评结论"),
    ),
    _InsightRule(
        kind="prefer",
        content="标题需要有冲突感",
        required_terms=(),
        any_terms=("冲突感", "标题有冲突", "标题更冲突"),
    ),
    _InsightRule(
        kind="avoid",
        content="不喜欢太官方的表达",
        required_terms=(),
        any_terms=("太官方", "像广告", "广告感", "官话", "营销味"),
    ),
    _InsightRule(
        kind="avoid",
        content="避免开头太慢",
        required_terms=("开头",),
        any_terms=("太慢", "铺垫太久", "进入太慢"),
    ),
)


def extract_comment_insights(comments_text: str) -> list[CommentInsight]:
    comments = _unique_comments(_comment_lines(comments_text))
    matches: dict[tuple[str, str], list[str]] = {}
    for comment in comments:
        for rule in _RULES:
            if rule.matches(comment):
                matches.setdefault((rule.kind, rule.content), []).append(comment)

    return [
        CommentInsight(kind=kind, content=content, source_comments=source_comments)
        for (kind, content), source_comments in matches.items()
    ]


def _comment_lines(comments_text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in comments_text.replace("\r", "\n").split("\n"):
        line = raw_line.strip(" \t-•#0123456789.、")
        if len(line) < 4:
            continue
        lines.append(line)
    return lines


def _unique_comments(comments: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for comment in comments:
        if comment in seen:
            continue
        seen.add(comment)
        result.append(comment)
    return result
