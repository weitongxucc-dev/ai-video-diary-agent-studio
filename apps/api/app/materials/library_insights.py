"""Pure material tag counts, taxonomy coverage and recommended gaps."""
from __future__ import annotations

from collections.abc import Sequence

from app.materials.contracts import MaterialAsset, effective_segment_usage_role


DEFAULT_MATERIAL_TAXONOMY = (
    "场景:办公",
    "场景:户外",
    "物体:电脑",
    "动作:打字",
    "内容:软件界面",
    "主体:人物",
    "镜头:特写",
)


_MATERIAL_COVERAGE_TERMS = {
    "主体:人物": ("主体:人物", "人物", "人像", "女性", "女人", "男子", "男人", "女孩", "男孩", "女士", "男士", "person", "people"),
    "场景:办公": ("场景:办公", "办公室", "办公环境", "office"),
    "场景:户外": ("场景:户外", "室外", "户外", "街道", "outdoor"),
    "物体:电脑": ("物体:电脑", "电脑", "笔记本", "计算机", "computer", "laptop"),
    "动作:打字": ("动作:打字", "打字", "typing"),
    "内容:软件界面": ("内容:软件界面", "软件界面", "应用界面", "app interface"),
    "镜头:特写": ("镜头:特写", "特写", "close-up"),
}


def material_library_insights(assets: Sequence[MaterialAsset]) -> dict[str, object]:
    segments = [segment for asset in assets for segment in asset.segments]
    stats: dict[str, dict[str, object]] = {}

    def blank(tag: str) -> dict[str, object]:
        return {"tag": tag, "asset_count": 0, "segment_count": 0, "duration_seconds": 0.0, "usage_count": 0}

    for asset in assets:
        # A material is counted once per tag name, however many of its
        # segments repeat that name and whichever list carried it. The
        # segment count keeps its own meaning beside it. Names are grouped
        # exactly as stored — the same equality the tag filter uses — so a
        # count here and the rows behind a click are always the same set.
        asset_tags = {
            value
            for value in (
                *asset.user_tags,
                *(tag for segment in asset.segments for tag in (*segment.system_tags, *segment.user_tags)),
            )
            if value
        }
        for tag in asset_tags:
            item = stats.setdefault(tag, blank(tag))
            item["asset_count"] = int(item["asset_count"]) + 1
    for segment in segments:
        duration = max(0.0, segment.end_seconds - segment.start_seconds)
        for tag in {*segment.system_tags, *segment.user_tags}:
            item = stats.setdefault(tag, blank(tag))
            item["segment_count"] = int(item["segment_count"]) + 1
            item["duration_seconds"] = float(item["duration_seconds"]) + duration
            item["usage_count"] = int(item["usage_count"]) + segment.usage_count
    coverage_categories = set()
    for tag in stats:
        # Existing taxonomy synonyms and role facts are used only for coverage;
        # original observations and click-to-filter tag counts stay unchanged.
        lowered = tag.casefold()
        for category, terms in _MATERIAL_COVERAGE_TERMS.items():
            if any(term in lowered for term in terms):
                coverage_categories.add(category)
    if any(effective_segment_usage_role(asset, segment) == "a_roll" for asset in assets for segment in asset.segments):
        coverage_categories.add("主体:人物")
    for tag in DEFAULT_MATERIAL_TAXONOMY:
        stats.setdefault(tag, blank(tag))
    ordered = sorted(
        stats.values(),
        key=lambda item: (-int(item["segment_count"]), str(item["tag"])),
    )
    return {
        "asset_count": len(assets),
        "segment_count": len(segments),
        "tags": [
            {**item, "duration_seconds": round(float(item["duration_seconds"]), 3)}
            for item in ordered
        ],
        "recommended_gaps": [
            str(item["tag"])
            for item in ordered
            if str(item["tag"]) not in coverage_categories
            and str(item["tag"]) in DEFAULT_MATERIAL_TAXONOMY
        ],
    }
