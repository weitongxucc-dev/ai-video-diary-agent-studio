from __future__ import annotations

from copy import deepcopy

from app.models import Episode, ScriptPackage


_COVER_TEMPLATES = (
    {
        "template_id": "pixel_diary",
        "name": "像素日记",
        "description": "适合观点、复盘和日常 AI 视频日记。",
        "accent": "#1d4ed8",
    },
    {
        "template_id": "tool_review",
        "name": "工具测评",
        "description": "突出工具名、判断和推荐等级。",
        "accent": "#0f766e",
    },
    {
        "template_id": "tutorial_steps",
        "name": "教程步骤",
        "description": "突出目标、步骤和最终效果。",
        "accent": "#b45309",
    },
)


def cover_templates() -> list[dict[str, str]]:
    return [dict(item) for item in _COVER_TEMPLATES]


def cover_copy_from_fields(headline: str, subline: str, tag: str) -> str:
    lines = [headline.strip(), subline.strip(), tag.strip()]
    return "\n".join(line for line in lines if line)


def cover_draft_from_episode(episode: Episode) -> dict[str, object]:
    if episode.script is None:
        raise ValueError("generate a script first")
    lines = [
        line.strip()
        for line in episode.script.cover_copy.splitlines()
        if line.strip()
    ]
    headline = lines[0] if len(lines) > 0 else episode.script.title_options[0]
    subline = lines[1] if len(lines) > 1 else "今天的AI产品观察"
    tag = lines[2] if len(lines) > 2 else f"DAY {episode.day_number}"
    template = cover_templates()[0]
    return {
        "episode_id": episode.id,
        "headline": headline,
        "subline": subline,
        "tag": tag,
        "template_id": template["template_id"],
        "preview_aspect_ratio": "4:3",
        "cover_copy": cover_copy_from_fields(headline, subline, tag),
        "templates": cover_templates(),
    }


def script_with_cover_copy(
    script: ScriptPackage,
    *,
    headline: str,
    subline: str,
    tag: str,
) -> ScriptPackage:
    updated = deepcopy(script)
    updated.cover_copy = cover_copy_from_fields(headline, subline, tag)
    return updated
