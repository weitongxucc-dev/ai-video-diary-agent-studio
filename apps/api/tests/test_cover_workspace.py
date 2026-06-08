from datetime import date

from app.cover_workspace import (
    cover_copy_from_fields,
    cover_draft_from_episode,
    cover_templates,
)
from app.models import Episode, EpisodeIntakeMode, EpisodeStatus, ScriptPackage


def _episode_with_script() -> Episode:
    return Episode(
        id="ep_1",
        day_number=7,
        episode_date=date(2026, 6, 8),
        status=EpisodeStatus.SCRIPT_READY,
        intake_mode=EpisodeIntakeMode.PLANNED,
        script=ScriptPackage(
            id="script_1",
            hook="hook",
            sections=[],
            title_options=["标题候选 A"],
            cover_copy="主标题\n副标题\nAI观察",
            publish_copy="publish",
            comment_prompt="comment",
        ),
    )


def test_cover_draft_reads_script_cover_copy():
    draft = cover_draft_from_episode(_episode_with_script())

    assert draft["headline"] == "主标题"
    assert draft["subline"] == "副标题"
    assert draft["tag"] == "AI观察"
    assert draft["preview_aspect_ratio"] == "4:3"
    assert draft["templates"][0]["template_id"] == "pixel_diary"


def test_cover_copy_from_fields_trims_and_limits_lines():
    assert cover_copy_from_fields(" 主标题 ", " 副标题 ", " 标签 ") == "主标题\n副标题\n标签"


def test_cover_templates_are_compact_and_named():
    templates = cover_templates()

    assert [item["template_id"] for item in templates] == [
        "pixel_diary",
        "tool_review",
        "tutorial_steps",
    ]
    assert all(item["name"] for item in templates)
