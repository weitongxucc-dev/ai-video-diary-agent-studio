from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def add_legacy_main_video_segments(
    script: object,
    material: object,
    duration: int,
    manifest: dict,
    draft_api: object,
    trange: Callable[[int, int], object],
    video_segment_class: object,
) -> None:
    template_effects = manifest.get("template_effects")
    transition_type = getattr(
        getattr(draft_api, "TransitionType", object),
        "叠加",
        None,
    )
    if (
        not isinstance(template_effects, dict)
        or not _has_effect_kind(template_effects, "transition")
        or transition_type is None
        or duration <= 6_000_000
    ):
        script.add_segment(video_segment_class(material, trange(0, duration)), "主视频")
        return

    first_duration = min(max(duration // 3, 1_500_000), 3_000_000)
    second_duration = duration - first_duration
    if second_duration <= 1_000_000:
        script.add_segment(video_segment_class(material, trange(0, duration)), "主视频")
        return

    first_segment = video_segment_class(
        material,
        trange(0, first_duration),
        source_timerange=trange(0, first_duration),
    )
    if hasattr(first_segment, "add_transition"):
        first_segment.add_transition(transition_type, duration="0.35s")
        template_effects["writer_transition_applied"] = True
    script.add_segment(first_segment, "主视频")
    script.add_segment(
        video_segment_class(
            material,
            trange(first_duration, second_duration),
            source_timerange=trange(first_duration, second_duration),
        ),
        "主视频",
    )


def add_legacy_template_tracks(
    script: object,
    manifest: dict,
    duration: int,
    draft_api: object,
    trange: Callable[[int, int], object],
) -> None:
    template_effects = manifest.get("template_effects")
    if not isinstance(template_effects, dict):
        return

    applied: list[str] = (
        ["简单转场"] if template_effects.get("writer_transition_applied") else []
    )
    warnings: list[str] = []
    try:
        _add_track_if_missing(script, draft_api.TrackType.audio, "背景音乐", mute=True)
        applied.append("背景音乐")
        title_track = "标题贴纸"
        keyword_track = "关键词高亮"
        _add_track_if_missing(script, draft_api.TrackType.text, title_track, relative_index=1)
        _add_track_if_missing(script, draft_api.TrackType.text, keyword_track, relative_index=2)
        applied.extend([title_track, keyword_track])
        effects = _effects_by_kind(template_effects.get("effects"))
        _add_title_template_texts(
            script,
            effects,
            duration,
            draft_api,
            trange,
            title_track,
            applied,
        )
        _add_keyword_template_texts(
            script,
            effects,
            duration,
            draft_api,
            trange,
            keyword_track,
            applied,
        )
        if _add_cover_track(script, manifest, duration, draft_api, trange):
            applied.append("封面")
    except Exception as exc:
        warnings.append(f"{type(exc).__name__}: {exc}")

    if applied:
        template_effects["writer_applied"] = applied
    if warnings:
        template_effects["writer_warnings"] = warnings


def _add_title_template_texts(
    script: object,
    effects: dict[str, dict],
    duration: int,
    draft_api: object,
    trange: Callable[[int, int], object],
    track_name: str,
    applied: list[str],
) -> None:
    cursor = 0
    cues = [
        ("intro", _payload_text(effects, "intro"), 2_000_000, "片头"),
        ("title_card", _payload_text(effects, "title_card"), 3_000_000, "标题卡"),
        (
            "chapter_bar",
            " / ".join(_payload_list(effects, "chapter_bar", "chapters")[:3]),
            3_000_000,
            "章节字幕条",
        ),
        (
            "key_sentence_sticker",
            _payload_text(effects, "key_sentence_sticker"),
            4_000_000,
            "重点句大字贴纸",
        ),
        ("outro", _payload_text(effects, "outro"), 3_000_000, "片尾"),
    ]
    for _, text, cue_duration, label in cues:
        if not text:
            continue
        remaining = duration - cursor
        if remaining <= 500_000:
            break
        actual_duration = min(cue_duration, remaining)
        segment = _text_segment(
            draft_api,
            trange(cursor, actual_duration),
            f"{label}：{text}",
            size=7.0 if label == "标题卡" else 5.0,
            transform_y=-0.76 if label in {"片头", "标题卡"} else -0.58,
            background="#0f3d91" if label != "重点句大字贴纸" else "#111827",
        )
        _add_text_animation(segment, draft_api)
        script.add_segment(segment, track_name)
        applied.append(label)
        cursor += actual_duration + 250_000


def _add_keyword_template_texts(
    script: object,
    effects: dict[str, dict],
    duration: int,
    draft_api: object,
    trange: Callable[[int, int], object],
    track_name: str,
    applied: list[str],
) -> None:
    cursor = min(3_000_000, max(0, duration // 4))
    keywords = _payload_list(effects, "keyword_highlight", "keywords")[:4]
    for keyword in keywords:
        remaining = duration - cursor
        if remaining <= 500_000:
            break
        actual_duration = min(2_000_000, remaining)
        segment = _text_segment(
            draft_api,
            trange(cursor, actual_duration),
            f"关键词：{keyword}",
            size=4.6,
            transform_x=-0.42,
            transform_y=0.56,
            background="#facc15",
            color=(0.05, 0.09, 0.16),
        )
        script.add_segment(segment, track_name)
        cursor += actual_duration + 250_000
    if keywords:
        applied.append("关键词高亮")

    b_roll_points = _payload_list(effects, "b_roll_insert", "insert_points")[:3]
    if not b_roll_points:
        return
    remaining = duration - cursor
    if remaining <= 500_000:
        return
    segment = _text_segment(
        draft_api,
        trange(cursor, min(3_000_000, remaining)),
        f"B-roll 插入点：{' / '.join(b_roll_points)}",
        size=4.3,
        transform_x=0.35,
        transform_y=0.56,
        background="#dbeafe",
        color=(0.04, 0.16, 0.42),
    )
    script.add_segment(segment, track_name)
    applied.append("B-roll 插入点")


def _add_cover_track(
    script: object,
    manifest: dict,
    duration: int,
    draft_api: object,
    trange: Callable[[int, int], object],
) -> bool:
    cover_path = manifest.get("artifacts", {}).get("cover_path")
    if not isinstance(cover_path, str) or not Path(cover_path).exists():
        return False
    _add_track_if_missing(script, draft_api.TrackType.video, "封面", relative_index=6)
    material = draft_api.VideoMaterial(cover_path, material_name="封面")
    segment = draft_api.VideoSegment(
        material,
        trange(0, min(duration or 3_000_000, 3_000_000)),
        clip_settings=draft_api.ClipSettings(
            alpha=0.72,
            scale_x=0.34,
            scale_y=0.34,
            transform_x=0.48,
            transform_y=-0.58,
        ),
    )
    script.add_segment(segment, "封面")
    return True


def _text_segment(
    draft_api: object,
    timerange: object,
    text: str,
    *,
    size: float,
    transform_x: float = 0,
    transform_y: float = 0,
    background: str,
    color: tuple[float, float, float] = (1, 1, 1),
) -> object:
    return draft_api.TextSegment(
        text,
        timerange,
        style=draft_api.TextStyle(
            align=1,
            auto_wrapping=True,
            bold=True,
            color=color,
            max_line_width=0.78,
            size=size,
        ),
        background=draft_api.TextBackground(
            alpha=0.92,
            color=background,
            height=0.16,
            width=0.72,
        ),
        border=draft_api.TextBorder(width=18),
        clip_settings=draft_api.ClipSettings(
            transform_x=transform_x,
            transform_y=transform_y,
        ),
    )


def _add_text_animation(segment: object, draft_api: object) -> None:
    intro = getattr(getattr(draft_api, "TextIntro", object), "向上滑动", None)
    outro = getattr(getattr(draft_api, "TextOutro", object), "渐隐", None)
    if intro is not None and hasattr(segment, "add_animation"):
        segment.add_animation(intro, "0.25s")
    if outro is not None and hasattr(segment, "add_animation"):
        segment.add_animation(outro, "0.25s")


def _add_track_if_missing(
    script: object,
    track_type: object,
    track_name: str,
    **kwargs: object,
) -> None:
    try:
        script.add_track(track_type, track_name, **kwargs)
    except NameError:
        return


def _effects_by_kind(effects: object) -> dict[str, dict]:
    if not isinstance(effects, list):
        return {}
    return {
        str(effect["kind"]): effect
        for effect in effects
        if isinstance(effect, dict) and effect.get("kind")
    }


def _has_effect_kind(template_effects: dict, kind: str) -> bool:
    effects = template_effects.get("effects")
    if not isinstance(effects, list):
        return False
    return any(
        isinstance(effect, dict) and effect.get("kind") == kind
        for effect in effects
    )


def _payload_text(effects: dict[str, dict], kind: str) -> str:
    payload = effects.get(kind, {}).get("payload")
    if not isinstance(payload, dict):
        return ""
    value = payload.get("text")
    return str(value) if value else ""


def _payload_list(
    effects: dict[str, dict],
    kind: str,
    key: str,
) -> list[str]:
    payload = effects.get(kind, {}).get("payload")
    if not isinstance(payload, dict):
        return []
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]
