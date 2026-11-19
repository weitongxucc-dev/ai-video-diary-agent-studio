"""Preserve an explicit track restriction from the consumed user input."""
from __future__ import annotations

import re


CLIP_TAIL_UNAVAILABLE = (
    "当前只能调整单个镜头的源片起点并保留它的时间槽，不能单独裁短这个镜头的末尾。"
    "按区间裁短整条时间线会同时删除这段画面、配音和字幕；本次作品保持原样。"
)
LINKED_EDIT_OUTSIDE_SCOPE = "这项操作会联动画面、声音和字幕，超出本次指定的编辑范围。当前作品保持原样。"


def _track_only(message: str, *, clip_tail: bool = False) -> bool:
    target = r"(?:镜头|画面|视频轨|音轨|配音|原声|字幕)"
    explicit_limit = re.search(rf"(?:只(?!要是)|仅)[^，。！？；,!?;\n]{{0,22}}{target}", message)
    preserve_other_track = re.search(
        rf"(?:不(?:要|能|得)?(?:改|动|剪|裁|影响)|保留|保持)[^，。！？；,!?;\n]{{0,14}}{target}"
        rf"|{target}[^，。！？；,!?;\n]{{0,10}}(?:别动|不(?:变|动|改)|保持原样)", message)
    restricted_link = re.search(
        r"(?:别|(?:不|勿)(?:要|能|得)?)[^，。！？；\n]{0,10}(?:全轨|所有轨道|各轨道|同步|联动)[^，。！？；\n]{0,8}(?:删|裁|剪|移除)"
        r"|\b(?:don['’]t|do not|never|without)\b[^,.!?;\n]{0,32}\b(?:all tracks|across tracks)\b",
        message, re.IGNORECASE)
    explicit_link = re.search(
        r"(?:(?:全轨|所有轨道|各轨道)[^，。！？；\n]{0,12}(?:删|裁|剪|移除)|"
        r"(?:删|裁|剪|移除)[^，。！？；\n]{0,10}(?:全轨|所有轨道|各轨道)|"
        r"同步(?:删|裁|剪|移除)|(?:across|on) all tracks|all tracks together)", message, re.IGNORECASE)
    english_limit = re.search(
        r"\b(?:only|just)\b[^,.!?;\n]{0,35}\b(?:shot|clip|video|picture|audio|subtitles?)\b"
        r"|\b(?:keep|leave)\b[^,.!?;\n]{0,32}\b(?:audio|subtitles?)\b[^,.!?;\n]{0,18}\b(?:unchanged|intact)\b"
        r"|\b(?:shot|clip|video|picture|audio|subtitles?)\b[^,.!?;\n]{0,10}\b(?:only|alone)\b",
        message, re.IGNORECASE)
    return bool(restricted_link or preserve_other_track or ((explicit_limit or english_limit or clip_tail) and not explicit_link))


def clip_tail_blocker(message: str) -> str | None:
    """The currently declared TrimClip contract can move only the in-point."""
    clip_tail = re.search(
        r"(?:镜头|视频片段|画面片段)[^，。！？；\n]{0,22}(?:末尾|最后|尾部|结尾|尾端|出点|结束(?:时间|位置)?)"
        r"|\b(?:end|tail|out[ -]?point)\b[^,.!?;\n]{0,24}\b(?:shot|clip)\b"
        r"|\b(?:shot|clip)\b[^,.!?;\n]{0,24}\b(?:end|tail|out[ -]?point)\b", message, re.IGNORECASE)
    shortening = re.search(r"裁|剪|缩短|削短|删除|改(?:到|成|为)|\b(?:trim|shorten|cut|set|remove)\b", message, re.IGNORECASE)
    return CLIP_TAIL_UNAVAILABLE if clip_tail and shortening and _track_only(message, clip_tail=True) else None


def edit_scope_blocker(source: dict | None, *, linked: bool = False) -> str | None:
    if not source or not source.get("assistant_turn_id"):
        return None
    message = source.get("raw_command") or ""
    return clip_tail_blocker(message) or (LINKED_EDIT_OUTSIDE_SCOPE if linked and _track_only(message) else None)


def native_edit_blocker(source: dict, operation: str, arguments: dict) -> str | None:
    starts_write = (
        operation in {"submit_task_command", "register_task_artifacts"}
        or operation == "native_task_file" and arguments.get("action") in {"write", "mkdir"}
        or operation == "open_task_working_context" and bool(arguments.get("input_refs"))
    )
    return edit_scope_blocker(source) if starts_write else None
