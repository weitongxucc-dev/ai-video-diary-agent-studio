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


def restructure_subtitle(track, operation: str, cue_id: str, *, split_offset=None, split_time_seconds=None):
    """Change caption structure on one language track, never the narration clock."""
    cues = sorted(track.cues, key=lambda cue: cue.start_seconds)
    index = next((index for index, cue in enumerate(cues) if cue.id == cue_id), None)
    if index is None:
        raise ValueError("所选字幕已变化，请重新选择")
    cue = cues[index]
    before = [cue]
    if operation == "SplitSubtitleCue":
        if type(split_offset) is not int or not 0 < split_offset < len(cue.text):
            raise ValueError("请选择字幕文字内部的拆分位置")
        left, right = cue.text[:split_offset], cue.text[split_offset:]
        if not left.strip() or not right.strip():
            raise ValueError("拆分后的两条字幕都必须包含文字")
        if split_time_seconds is None or not cue.start_seconds + .04 <= split_time_seconds <= cue.end_seconds - .04:
            raise ValueError("拆分时间必须在字幕内部，两侧至少保留0.04秒")
        from uuid import uuid4
        pieces = [replace(cue, text=left, end_seconds=split_time_seconds,
            line_breaks=[p for p in cue.line_breaks if p < split_offset], emphasis=[term for term in cue.emphasis if term in left]),
            replace(cue, id=f"cue_{uuid4().hex}", text=right, start_seconds=split_time_seconds,
                line_breaks=[p-split_offset for p in cue.line_breaks if p > split_offset], emphasis=[term for term in cue.emphasis if term in right])]
        cues[index:index+1] = pieces
    elif operation == "MergeSubtitleCue":
        if index + 1 >= len(cues):
            raise ValueError("当前字幕后面没有可合并的条目")
        following = cues[index+1]
        if following.start_seconds < cue.end_seconds - .001:
            raise ValueError("字幕存在重叠，请先调整时间后合并")
        separator = " " if track.language == "en-US" and not cue.text[-1].isspace() and not following.text[0].isspace() else ""
        offset = len(cue.text) + len(separator)
        pieces = [replace(cue, text=cue.text + separator + following.text, end_seconds=following.end_seconds,
            line_breaks=[*cue.line_breaks, *[offset+p for p in following.line_breaks]],
            emphasis=list(dict.fromkeys([*cue.emphasis, *following.emphasis])))]
        before.append(following)
        cues[index:index+2] = pieces
    elif operation == "DeleteSubtitleCue":
        pieces = []
        cues.pop(index)
    else:
        raise ValueError("不支持的字幕结构操作")
    def describe(items):
        return " / ".join(f"{item.start_seconds:.3f}–{item.end_seconds:.3f} 秒：{item.text}" for item in items)
    return replace(track, cues=cues, artifact_ref=None, transform_id=None), [{"cue_id":cue.id,
        "before":describe(before), "after":describe(pieces) or "删除此条字幕，保留该时段配音与画面", "occurrences":1}]


def normalize_subtitle_text_and_breaks(text, line_breaks):
    normalized=text.replace('\r\n','\n').replace('\r','\n')
    if '\n' not in normalized:
        return normalized,list(line_breaks or [])
    lines=normalized.split('\n')
    if any(not line.strip() for line in lines):
        raise ValueError('subtitle lines cannot be empty')
    inferred=[]
    cursor=0
    for line in lines[:-1]:
        cursor+=len(line)
        inferred.append(cursor)
    if line_breaks is not None and list(line_breaks)!=inferred:
        raise ValueError('subtitle text line breaks conflict with line_breaks')
    return ''.join(lines),inferred


def update_subtitle_cue(track,cue_id,*,text=None,start_seconds=None,end_seconds=None,line_breaks=None,emphasis=None):
    cue=next((cue for cue in track.cues if cue.id==cue_id),None)
    if cue is None:
        raise ValueError('所选字幕不存在')
    normalized,breaks=normalize_subtitle_text_and_breaks(text if text is not None else cue.text,
        line_breaks if line_breaks is not None else cue.line_breaks if text is None or text==cue.text else None)
    if not normalized.strip() or len(normalized)>4000:
        raise ValueError('字幕文字不能为空且不能超过4000字')
    updated=replace(cue,text=normalized,line_breaks=breaks,emphasis=cue.emphasis if emphasis is None else emphasis,
        start_seconds=cue.start_seconds if start_seconds is None else start_seconds,
        end_seconds=cue.end_seconds if end_seconds is None else end_seconds)
    return replace(track,cues=[updated if item.id==cue_id else item for item in track.cues],artifact_ref=None,transform_id=None)
