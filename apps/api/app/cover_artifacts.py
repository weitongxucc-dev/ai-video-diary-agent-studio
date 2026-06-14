from __future__ import annotations

import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.config import Settings
from app.models import Episode


_COVER_RENDER_STYLES = {
    "pixel_diary": {
        "accent": "#2563eb",
        "soft": "#dbeafe",
        "label": "AI VIDEO DIARY",
    },
    "tool_review": {
        "accent": "#0f766e",
        "soft": "#ccfbf1",
        "label": "TOOL REVIEW",
    },
    "tutorial_steps": {
        "accent": "#b45309",
        "soft": "#ffedd5",
        "label": "TUTORIAL",
    },
}


def cover_preview_path(settings: Settings, episode_id: str) -> Path:
    safe_episode_id = re.sub(r"[^a-zA-Z0-9_-]", "_", episode_id)
    return (
        Path(settings.draft_output_path).expanduser()
        / "_cover_previews"
        / safe_episode_id
        / "cover.png"
    )


def cover_preview_url(episode_id: str, cover_path: Path) -> str:
    version = int(cover_path.stat().st_mtime) if cover_path.exists() else 0
    return f"/api/episodes/{episode_id}/cover/image?v={version}"


def cover_artifact_payload(settings: Settings, episode: Episode) -> dict[str, object]:
    path = cover_preview_path(settings, episode.id)
    if not path.exists():
        return {
            "episode_id": episode.id,
            "cover_path": None,
            "preview_url": None,
            "cover_source": "none",
            "width": None,
            "height": None,
        }
    with Image.open(path) as image:
        width, height = image.size
    return {
        "episode_id": episode.id,
        "cover_path": str(path),
        "preview_url": cover_preview_url(episode.id, path),
        "cover_source": "pixel_template",
        "width": width,
        "height": height,
    }


def write_cover(cover_path: Path, episode: Episode) -> None:
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    style = _COVER_RENDER_STYLES.get(
        episode.cover_template_id,
        _COVER_RENDER_STYLES["pixel_diary"],
    )
    image = Image.new("RGB", (1200, 900), "#fffaf0")
    draw = ImageDraw.Draw(image)
    title_font = font(68)
    body_font = font(38)
    small_font = font(28)

    accent = style["accent"]
    soft = style["soft"]
    draw.rectangle((0, 0, 1200, 900), fill="#fffaf0")
    draw.rectangle((42, 42, 1158, 858), outline="#111827", width=6)
    draw.rectangle((64, 64, 1136, 132), fill=soft, outline="#111827", width=4)
    draw.text((86, 82), style["label"], font=small_font, fill=accent)
    draw.text((898, 82), f"DAY {episode.day_number}", font=small_font, fill="#111827")

    cover_lines = (
        episode.script.cover_copy.splitlines()
        if episode.script and episode.script.cover_copy
        else [f"AI视频日记 第{episode.day_number}天"]
    )
    headline = cover_lines[0]
    subline = cover_lines[1] if len(cover_lines) > 1 else "今天的AI产品观察"
    tag = cover_lines[2] if len(cover_lines) > 2 else "待导入剪映"
    for index, line in enumerate(textwrap.wrap(headline, width=13)[:2]):
        draw.text((92, 210 + index * 84), line, font=title_font, fill="#111827")
    for index, line in enumerate(textwrap.wrap(subline, width=22)[:3]):
        draw.text((96, 430 + index * 52), line, font=body_font, fill="#374151")

    draw.rectangle((94, 680, 420, 760), fill=accent, outline="#111827", width=4)
    draw.text((128, 704), tag[:8], font=body_font, fill="#ffffff")
    draw.rectangle((804, 600, 1036, 790), fill=soft, outline="#111827", width=5)
    draw.rectangle((850, 640, 990, 720), fill="#ffffff", outline="#111827", width=4)
    draw.rectangle((884, 670, 902, 690), fill=accent)
    draw.rectangle((936, 670, 954, 690), fill=accent)
    draw.rectangle((910, 742, 958, 758), fill="#111827")
    image.save(cover_path)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()
