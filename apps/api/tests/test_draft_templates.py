from datetime import date

import pytest

from app.draft_templates import build_draft_template_manifest
from app.models import Episode, EpisodeStatus, ScriptPackage, ScriptSection


def make_episode(content_template_id: str) -> Episode:
    return Episode(
        id=f"ep_{content_template_id}",
        day_number=9,
        episode_date=date(2026, 6, 8),
        status=EpisodeStatus.SCRIPT_READY,
        content_template_id=content_template_id,
        script=ScriptPackage(
            id="script_9",
            hook="先说结论，这个 AI 工具适合小团队做粗剪提效。",
            sections=[
                ScriptSection(
                    id="s1",
                    title="结论",
                    estimated_seconds=20,
                    content="它的优势是把重复剪辑动作自动化，但复杂审美仍然需要人工检查。",
                ),
                ScriptSection(
                    id="s2",
                    title="实测",
                    estimated_seconds=35,
                    content="我用一段口播素材测试了字幕、封面和 B-roll 插入点。",
                ),
            ],
            title_options=["AI 粗剪工具，真的能省时间吗？"],
            cover_copy="AI粗剪工具\n真实测评",
            publish_copy="今天测一个粗剪工作流。",
            comment_prompt="你会把粗剪交给 AI 吗？",
        ),
    )


@pytest.mark.parametrize(
    ("content_template_id", "expected_template_id"),
    [
        ("video_diary", "diary_minimal"),
        ("tool_review", "tool_review"),
        ("tutorial_demo", "tutorial_demo"),
    ],
)
def test_draft_template_manifest_selects_jianying_style(
    content_template_id: str,
    expected_template_id: str,
) -> None:
    manifest = build_draft_template_manifest(
        episode=make_episode(content_template_id),
        artifacts={"subtitle_path": "/draft/subtitle.srt", "cover_path": "/draft/cover.png"},
        video_path="/processed/talk_preview.mp4",
    )

    assert manifest["content_template_id"] == content_template_id
    assert manifest["template_id"] == expected_template_id
    assert manifest["template_name"]


def test_draft_template_manifest_declares_required_tracks_and_effects() -> None:
    manifest = build_draft_template_manifest(
        episode=make_episode("tool_review"),
        artifacts={"subtitle_path": "/draft/subtitle.srt", "cover_path": "/draft/cover.png"},
        video_path="/processed/talk_preview.mp4",
    )

    assert [track["number"] for track in manifest["tracks"]] == [1, 2, 3, 4, 5, 6]
    assert [track["label"] for track in manifest["tracks"]] == [
        "主视频",
        "字幕",
        "标题贴纸",
        "关键词高亮",
        "背景音乐",
        "封面",
    ]
    assert manifest["tracks"][0]["asset_refs"] == ["/processed/talk_preview.mp4"]
    assert manifest["tracks"][1]["asset_refs"] == ["/draft/subtitle.srt"]
    assert manifest["tracks"][5]["asset_refs"] == ["/draft/cover.png"]

    effect_kinds = {effect["kind"] for effect in manifest["effects"]}
    assert effect_kinds == {
        "title_card",
        "chapter_bar",
        "keyword_highlight",
        "key_sentence_sticker",
        "cover",
        "b_roll_insert",
        "transition",
        "background_music",
        "intro",
        "outro",
    }
    assert any(effect["label"] == "标题卡" for effect in manifest["effects"])
    assert any(effect["label"] == "章节字幕条" for effect in manifest["effects"])
    assert any(effect["label"] == "关键词高亮" for effect in manifest["effects"])
    assert any(effect["label"] == "背景音乐音量" for effect in manifest["effects"])
    assert manifest["import_preview"][0]["track"] == "轨道 1：主视频"
