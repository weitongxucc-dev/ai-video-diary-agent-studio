"""Read-only render schedule derived from versioned subtitle tracks, never shots."""
from dataclasses import dataclass,field,replace
from fractions import Fraction
import math
import re


@dataclass
class TimedCaption:
    start_seconds: float
    end_seconds: float
    text: str
    subtitle_style: dict
    subtitle_emphasis: list[str] = field(default_factory=list)
    subtitle_emphasis_ranges: list[tuple[int,int]] | None = None


def new_subtitle_gap_intervals(before_track, after_track, duration_seconds: float) -> list[tuple[float, float]]:
    """Return previously covered intervals left blank by a same-track edit.

    Call after timing validation, with the original and final candidate of one
    valid language track. Visibility settings and other languages do not affect
    this track's coverage. Existing blank intervals are never reported.
    """
    if before_track.status != "valid" or after_track.status != "valid":
        return []
    if before_track.language != after_track.language:
        raise ValueError("新增空白须比较同一语言轨")
    boundaries = sorted({0.0, duration_seconds, *(
        min(duration_seconds, max(0.0, time))
        for track in (before_track, after_track)
        for cue in track.cues
        for time in (cue.start_seconds, cue.end_seconds)
    )})
    gaps = []
    for start, end in zip(boundaries, boundaries[1:]):
        covered_before = any(cue.start_seconds <= start and cue.end_seconds >= end for cue in before_track.cues)
        covered_after = any(cue.start_seconds <= start and cue.end_seconds >= end for cue in after_track.cues)
        if covered_before and not covered_after:
            if gaps and gaps[-1][1] == start:
                gaps[-1] = (gaps[-1][0], end)
            else:
                gaps.append((start, end))
    return gaps


def subtitle_schedule(tracks, duration_seconds: float) -> list[TimedCaption]:
    if any(type(track.style.get("enabled", True)) is not bool for track in tracks):
        raise ValueError("字幕显示设置必须为布尔值")
    visible=[track for track in tracks if track.status=="valid" and track.style.get("enabled",True)]
    boundaries=set()
    for track in visible:
        previous=0.0
        for cue in sorted(track.cues,key=lambda cue:cue.start_seconds):
            if not all(math.isfinite(time) for time in (cue.start_seconds,cue.end_seconds)) or not 0<=cue.start_seconds<cue.end_seconds<=duration_seconds+.03:
                raise ValueError("字幕区间必须位于成片内")
            if cue.start_seconds < previous-.001:
                raise ValueError("同一字幕轨的条目重叠，请先修正时轴")
            previous=cue.end_seconds
            boundaries.update((cue.start_seconds,min(duration_seconds,cue.end_seconds)))
    result=[]
    ordered=sorted(boundaries)
    for start,end in zip(ordered,ordered[1:]):
        active=[(track,cue) for track in visible for cue in track.cues if cue.start_seconds<=start+.0001 and cue.end_seconds>=end-.0001]
        positions={track.style.get("position","bottom") for track,cue in active}
        for position in sorted(positions):
            group=[(track,cue) for track,cue in active if track.style.get("position","bottom")==position]
            source=next((track for track,cue in group if track.source_language is None),group[0][0])
            translation_first=source.style.get("bilingual_order")=="translation_then_source"
            group.sort(key=lambda item:(item[0].source_language is None)==translation_first)
            lines=[]
            ranges=[]
            offset=0
            for track,cue in group:
                text=cue.text.replace("\r\n","\n").replace("\r","\n")
                if cue.line_breaks:
                    text=text.replace("\n","")
                    points=[0,*cue.line_breaks,len(text)]
                    text="\n".join(text[a:b] for a,b in zip(points,points[1:]))
                lines.append(text)
                compact=re.sub(r"\s+","",text)
                for term in cue.emphasis:
                    term=re.sub(r"\s+","",term)
                    if term:
                        ranges.extend((offset+match.start(),offset+match.end()) for match in re.finditer(re.escape(term),compact))
                offset+=len(compact)
            caption=TimedCaption(start,end,"\n".join(lines),dict(source.style),subtitle_emphasis_ranges=ranges)
            # Merge event boundaries introduced only by another screen position.
            prior=next((item for item in reversed(result) if item.subtitle_style.get("position","bottom")==position),None)
            if prior and abs(prior.end_seconds-start)<.0001 and prior.text==caption.text and prior.subtitle_style==caption.subtitle_style and prior.subtitle_emphasis_ranges==ranges:
                prior.end_seconds=end
            else:
                result.append(caption)
    return sorted(result,key=lambda item:(item.start_seconds,item.subtitle_style.get("position","bottom")))


def narration_schedule(cues, duration_seconds: float) -> list[TimedCaption]:
    """Initial captions use the narration clock, independent of shot selection/order."""
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("旁白字幕区间必须位于成片内")
    result = []
    cursor = Fraction(0)
    duration = Fraction(str(duration_seconds))
    for cue in cues:
        if cue.timeline_start_seconds is not None:
            if not math.isfinite(cue.timeline_start_seconds):
                raise ValueError("旁白字幕起点无效或重叠")
            start = Fraction(str(cue.timeline_start_seconds))
            if start < cursor:
                raise ValueError("旁白字幕起点无效或重叠")
            cursor = start
        if not math.isfinite(cue.duration_seconds):
            raise ValueError("旁白字幕区间必须位于成片内")
        end = cursor + Fraction(str(cue.duration_seconds))
        if end <= cursor or end > duration + Fraction(3, 100):
            raise ValueError("旁白字幕区间必须位于成片内")
        result.append(TimedCaption(float(cursor), float(min(end, duration)), cue.text,
            dict(cue.subtitle_style), list(cue.subtitle_emphasis), cue.subtitle_emphasis_ranges))
        cursor = end
    return result


def schedule_srt(schedule: list[TimedCaption]) -> str:
    def timestamp(seconds):
        ms=round(seconds*1000)
        hours,ms=divmod(ms,3600000)
        minutes,ms=divmod(ms,60000)
        seconds,ms=divmod(ms,1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"
    # SRT has no screen-position layering: combine simultaneous captions.
    boundaries=sorted({time for item in schedule for time in (item.start_seconds,item.end_seconds)})
    intervals=[]
    for start,end in zip(boundaries,boundaries[1:]):
        text="\n".join(item.text for item in schedule if item.start_seconds<=start and item.end_seconds>=end)
        if not text:
            continue
        if intervals and intervals[-1][1]==start and intervals[-1][2]==text:
            intervals[-1]=(intervals[-1][0],end,text)
        else:
            intervals.append((start,end,text))
    return "\n\n".join(f"{index}\n{timestamp(start)} --> {timestamp(end)}\n{text}" for index,(start,end,text) in enumerate(intervals,1)) + ("\n" if intervals else "")


def retime_subtitle_tracks(tracks, before_cues, after_cues):
    """Move existing caption boundaries with the narration clock, not shot order.

    This preserves manual offsets proportionally; it is not acoustic alignment.
    """
    if [cue.id for cue in before_cues] != [cue.id for cue in after_cues]:
        raise ValueError("配音重定时缺少一致的字幕区间")
    spans = []
    before_end = after_end = 0.0
    for before, after in zip(before_cues, after_cues, strict=True):
        if any(not math.isfinite(cue.duration_seconds) or cue.duration_seconds <= 0 for cue in (before, after)):
            raise ValueError("配音字幕区间时长无效")
        spans.append((before_end, before_end + before.duration_seconds, after_end, after_end + after.duration_seconds))
        before_end += before.duration_seconds
        after_end += after.duration_seconds
    if all(before.duration_seconds == after.duration_seconds for before, after in zip(before_cues, after_cues, strict=True)):
        return tracks

    def move(time):
        if not math.isfinite(time) or not 0 <= time <= before_end + .03:
            raise ValueError("原字幕超出配音时间轴，无法自动重定时")
        time = min(time, before_end)
        for old_start, old_end, new_start, new_end in spans:
            if time <= old_end:
                return round(new_start + (time - old_start) / (old_end - old_start) * (new_end - new_start), 3)
        raise ValueError("缺少字幕对应的配音区间")

    result = []
    for track in tracks:
        cues = [replace(cue, start_seconds=move(cue.start_seconds), end_seconds=move(cue.end_seconds)) for cue in track.cues]
        # Validate hidden tracks too, so re-enabling them cannot revive invalid times.
        subtitle_schedule([replace(track, status="valid", style={**track.style, "enabled": True}, cues=cues)], after_end)
        result.append(replace(track, cues=cues, artifact_ref=None, transform_id=None))
    return result
