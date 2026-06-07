from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from app.content_templates import ContentTemplate, get_content_template
from app.models import Episode, ScriptPackage


class DraftTemplateTrackManifest(TypedDict):
    number: int
    key: str
    label: str
    purpose: str
    asset_refs: list[str]


class DraftTemplateEffectManifest(TypedDict):
    kind: str
    label: str
    track_number: int
    enabled: bool
    payload: dict[str, object]


class DraftTemplateImportPreview(TypedDict):
    track: str
    purpose: str
    asset_refs: list[str]
    effect_labels: list[str]


class DraftTemplateManifest(TypedDict):
    content_template_id: str
    template_id: str
    template_name: str
    tracks: list[DraftTemplateTrackManifest]
    effects: list[DraftTemplateEffectManifest]
    import_preview: list[DraftTemplateImportPreview]


@dataclass(frozen=True)
class DraftTemplatePreset:
    template_id: str
    name: str
    intro_label: str
    outro_label: str
    transition_style: str
    keyword_style: str
    background_music_volume_db: int
    b_roll_hint: str


DRAFT_TRACKS: tuple[dict[str, str | int], ...] = (
    {"number": 1, "key": "main_video", "label": "主视频", "purpose": "承载口播或屏幕录制主素材"},
    {"number": 2, "key": "subtitle", "label": "字幕", "purpose": "导入脚本估算字幕或 ASR 字幕"},
    {"number": 3, "key": "title_sticker", "label": "标题贴纸", "purpose": "标题卡、重点句、片头片尾"},
    {"number": 4, "key": "keyword_highlight", "label": "关键词高亮", "purpose": "突出工具名、结论和步骤关键词"},
    {"number": 5, "key": "background_music", "label": "背景音乐", "purpose": "预留低音量背景音乐层"},
    {"number": 6, "key": "cover", "label": "封面", "purpose": "封面图与导入草稿封面"},
)


TEMPLATE_PRESETS: dict[str, DraftTemplatePreset] = {
    "diary_minimal": DraftTemplatePreset(
        template_id="diary_minimal",
        name="日记极简剪映模板",
        intro_label="今日观察",
        outro_label="明天继续复盘",
        transition_style="轻切",
        keyword_style="判断句下划线",
        background_music_volume_db=-22,
        b_roll_hint="真实场景或笔记截图",
    ),
    "tool_review": DraftTemplatePreset(
        template_id="tool_review",
        name="AI 工具测评剪映模板",
        intro_label="结论先行",
        outro_label="适用边界",
        transition_style="章节硬切",
        keyword_style="工具名与结论高亮",
        background_music_volume_db=-20,
        b_roll_hint="工具界面截图、屏幕录制或产品页",
    ),
    "tutorial_demo": DraftTemplatePreset(
        template_id="tutorial_demo",
        name="教程演示剪映模板",
        intro_label="目标预览",
        outro_label="步骤复盘",
        transition_style="步骤切换",
        keyword_style="步骤编号与错误提醒",
        background_music_volume_db=-24,
        b_roll_hint="步骤截图、最终效果图或录屏片段",
    ),
}


def build_draft_template_manifest(
    episode: Episode | None,
    artifacts: dict[str, object],
    video_path: str,
) -> DraftTemplateManifest:
    content_template = _content_template_for(episode)
    preset = TEMPLATE_PRESETS.get(
        content_template.jianying_style,
        TEMPLATE_PRESETS["diary_minimal"],
    )
    script = episode.script if episode else None
    tracks = _build_tracks(artifacts=artifacts, video_path=video_path, script=script)
    effects = _build_effects(
        preset=preset,
        script=script,
        artifacts=artifacts,
    )
    return {
        "content_template_id": content_template.template_id,
        "template_id": preset.template_id,
        "template_name": preset.name,
        "tracks": tracks,
        "effects": effects,
        "import_preview": _build_import_preview(tracks, effects),
    }


def _content_template_for(episode: Episode | None) -> ContentTemplate:
    template_id = episode.content_template_id if episode else "video_diary"
    try:
        return get_content_template(template_id)
    except KeyError:
        return get_content_template("video_diary")


def _build_tracks(
    artifacts: dict[str, object],
    video_path: str,
    script: ScriptPackage | None,
) -> list[DraftTemplateTrackManifest]:
    subtitle_path = _string_or_none(artifacts.get("subtitle_path"))
    cover_path = _string_or_none(artifacts.get("cover_path"))
    text_refs = ["template:title_card", "template:intro", "template:outro"]
    if script and script.hook:
        text_refs.append("template:key_sentence_sticker")

    asset_refs = {
        "main_video": [video_path],
        "subtitle": [subtitle_path] if subtitle_path else [],
        "title_sticker": text_refs,
        "keyword_highlight": ["template:keyword_highlight"],
        "background_music": ["template:background_music"],
        "cover": [cover_path] if cover_path else [],
    }
    return [
        {
            **track,
            "asset_refs": asset_refs.get(str(track["key"]), []),
        }
        for track in DRAFT_TRACKS
    ]


def _build_effects(
    preset: DraftTemplatePreset,
    script: ScriptPackage | None,
    artifacts: dict[str, object],
) -> list[DraftTemplateEffectManifest]:
    title = _title_for(script)
    sections = _sections_for(script)
    keywords = _keywords_for(script)
    return [
        {
            "kind": "title_card",
            "label": "标题卡",
            "track_number": 3,
            "enabled": True,
            "payload": {"text": title, "style": preset.name},
        },
        {
            "kind": "chapter_bar",
            "label": "章节字幕条",
            "track_number": 2,
            "enabled": bool(sections),
            "payload": {"chapters": sections},
        },
        {
            "kind": "keyword_highlight",
            "label": "关键词高亮",
            "track_number": 4,
            "enabled": bool(keywords),
            "payload": {"keywords": keywords, "style": preset.keyword_style},
        },
        {
            "kind": "key_sentence_sticker",
            "label": "重点句大字贴纸",
            "track_number": 3,
            "enabled": bool(script and script.hook),
            "payload": {"text": script.hook if script else ""},
        },
        {
            "kind": "cover",
            "label": "封面",
            "track_number": 6,
            "enabled": bool(_string_or_none(artifacts.get("cover_path"))),
            "payload": {"asset_ref": _string_or_none(artifacts.get("cover_path"))},
        },
        {
            "kind": "b_roll_insert",
            "label": "B-roll 插入点",
            "track_number": 1,
            "enabled": True,
            "payload": {"hint": preset.b_roll_hint, "insert_points": sections or ["主视频中段"]},
        },
        {
            "kind": "transition",
            "label": "简单转场",
            "track_number": 1,
            "enabled": True,
            "payload": {"style": preset.transition_style, "scope": "章节之间"},
        },
        {
            "kind": "background_music",
            "label": "背景音乐音量",
            "track_number": 5,
            "enabled": True,
            "payload": {"volume_db": preset.background_music_volume_db},
        },
        {
            "kind": "intro",
            "label": "片头",
            "track_number": 3,
            "enabled": True,
            "payload": {"text": preset.intro_label},
        },
        {
            "kind": "outro",
            "label": "片尾",
            "track_number": 3,
            "enabled": True,
            "payload": {"text": script.comment_prompt if script else preset.outro_label},
        },
    ]


def _build_import_preview(
    tracks: list[DraftTemplateTrackManifest],
    effects: list[DraftTemplateEffectManifest],
) -> list[DraftTemplateImportPreview]:
    return [
        {
            "track": f"轨道 {track['number']}：{track['label']}",
            "purpose": track["purpose"],
            "asset_refs": track["asset_refs"],
            "effect_labels": [
                str(effect["label"])
                for effect in effects
                if effect["track_number"] == track["number"]
            ],
        }
        for track in tracks
    ]


def _title_for(script: ScriptPackage | None) -> str:
    if script and script.title_options:
        return script.title_options[0]
    if script and script.cover_copy:
        return script.cover_copy.splitlines()[0]
    return "AI视频日记"


def _sections_for(script: ScriptPackage | None) -> list[str]:
    if not script:
        return []
    return [section.title for section in script.sections if section.title]


def _keywords_for(script: ScriptPackage | None) -> list[str]:
    if not script:
        return []
    candidates = [*script.title_options, *[section.title for section in script.sections]]
    keywords: list[str] = []
    for candidate in candidates:
        for token in _split_keyword_candidate(candidate):
            if token not in keywords:
                keywords.append(token)
            if len(keywords) >= 6:
                return keywords
    return keywords


def _split_keyword_candidate(value: str) -> list[str]:
    separators = ["，", "。", "？", "！", ",", "?", "!", "、", " "]
    tokens = [value]
    for separator in separators:
        tokens = [piece for token in tokens for piece in token.split(separator)]
    return [token.strip() for token in tokens if len(token.strip()) >= 2]


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
