"""Literal subtitle text edits; narration and caption time are never rewritten."""
from dataclasses import replace
import re


def replace_subtitle_text(track, find_text: str, replacement: str):
    if not find_text or find_text == replacement:
        raise ValueError("查找内容不能为空，替换内容须与原文不同")
    if any(char in find_text + replacement for char in "\r\n"):
        raise ValueError("查找替换不能包含换行，请使用字幕断行设置")
    cues, changes = [], []
    for cue in track.cues:
        matches = list(re.finditer(re.escape(find_text), cue.text))
        if not matches:
            cues.append(cue)
            continue
        text = cue.text.replace(find_text, replacement)
        if not text.strip():
            raise ValueError("替换会产生空字幕，请缩小查找内容或保留字幕文字")
        breaks = []
        for boundary in cue.line_breaks:
            if any(match.start() < boundary < match.end() for match in matches):
                continue
            moved = boundary + sum(len(replacement) - len(find_text) for match in matches if match.end() <= boundary)
            if 0 < moved < len(text):
                breaks.append(moved)
        emphasis = list(dict.fromkeys(term.replace(find_text, replacement) for term in cue.emphasis
            if term.replace(find_text, replacement).strip()))
        updated = replace(cue, text=text, line_breaks=sorted(set(breaks)), emphasis=emphasis)
        cues.append(updated)
        changes.append({"cue_id":cue.id, "before":cue.text, "after":text, "occurrences":len(matches)})
    if not changes:
        raise ValueError("当前字幕轨没有找到该文字")
    return replace(track, cues=cues, artifact_ref=None, transform_id=None), changes
