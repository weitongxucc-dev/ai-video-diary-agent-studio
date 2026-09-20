"""One interval map for ripple video edits, audio source ranges and captions."""
from dataclasses import replace
from uuid import uuid4

from app.audio_timeline import AudioTimeline, TimelineAudioClip
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.subtitle_schedule import subtitle_schedule
from app.visual_effects import clip_playback_rate
from app.graphic_overlays import splice_overlays


def splice_intervals(duration_ms: int, start_ms: int, end_ms: int, action: str):
    """Return (old start, old end, new start) for retained/copied time ranges."""
    if not 0 <= start_ms < end_ms <= duration_ms:
        raise ValueError("关联编辑区间必须位于成片内")
    if action == "delete":
        if start_ms == 0 and end_ms == duration_ms:
            raise ValueError("不能删除成片的全部内容")
        return [(a, b, c) for a, b, c in
            [(0, start_ms, 0), (end_ms, duration_ms, start_ms)] if a < b]
    if action == "duplicate":
        return [(a, b, c) for a, b, c in
            [(0, end_ms, 0), (start_ms, end_ms, end_ms),
             (end_ms, duration_ms, end_ms + end_ms - start_ms)] if a < b]
    raise ValueError("关联编辑只支持删除或复制")


def _pieces(start, end, intervals):
    for old_start, old_end, new_start in intervals:
        left, right = max(start, old_start), min(end, old_end)
        if right > left:
            yield left, right, new_start + left - old_start


def splice_audio(timeline: AudioTimeline, intervals) -> AudioTimeline:
    result = timeline.model_copy(deep=True)
    for track in result.tracks:
        clips = []
        for clip in track.clips:
            start, end = clip.timeline_start_ms, clip.timeline_start_ms + clip.duration_ms
            parts = list(_pieces(start, end, intervals))
            unchanged = parts == [(start, end, start)]
            if not unchanged and (track.locked or clip.locked):
                raise ValueError("关联编辑会改变已锁定音轨或音频片段，请先解锁")
            for index, (left, right, at) in enumerate(parts):
                # Keep the original envelope coordinates, including cuts through fades.
                clips.append(TimelineAudioClip.model_validate({**clip.model_dump(),
                    "id": clip.id if index == 0 else f"audio-{uuid4().hex[:16]}",
                    "source_start_ms": clip.source_start_ms + left - start,
                    "source_end_ms": clip.source_start_ms + right - start,
                    "timeline_start_ms": at,
                    "envelope_offset_ms": clip.envelope_offset_ms + left - start,
                    "envelope_duration_ms": clip.envelope_duration_ms or clip.duration_ms,
                }))
        track.clips = sorted(clips, key=lambda clip: clip.timeline_start_ms)
    return AudioTimeline.model_validate(result.model_dump())


def splice_subtitles(tracks, intervals, duration_seconds):
    result = []
    for track in tracks:
        cues = []
        for cue in track.cues:
            start, end = round(cue.start_seconds * 1000), round(cue.end_seconds * 1000)
            for index, (left, right, at) in enumerate(_pieces(start, end, intervals)):
                cues.append(replace(cue,
                    id=cue.id if index == 0 else f"{cue.id}-copy-{uuid4().hex[:8]}",
                    start_seconds=at / 1000, end_seconds=(at + right - left) / 1000))
        if not cues:
            continue
        cues.sort(key=lambda cue: cue.start_seconds)
        moved = replace(track, cues=cues, artifact_ref=None, transform_id=None)
        subtitle_schedule([replace(moved, status="valid", style={**moved.style, "enabled": True})], duration_seconds)
        result.append(moved)
    return result


def splice_clip(plan, tracks, audio, clip_id: str, action: str):
    """Cut/copy a complete shot and exactly the same interval on linked tracks.

    Original narration artifacts remain immutable. The versioned audio graph
    represents the edited voice; subsequent synthesis must respect that graph.
    """
    if plan.status == "rendering":
        raise ValueError("正在渲染，不能修改时间线")
    selected = next((clip for clip in plan.clips if clip.id == clip_id), None)
    if selected is None:
        raise ValueError("所选镜头不存在")
    start, end = round(selected.timeline_start_seconds * 1000), round(selected.timeline_end_seconds * 1000)
    duration = round(plan.duration_seconds * 1000)
    intervals = splice_intervals(duration, start, end, action)
    return _splice_plan(plan, tracks, audio, intervals,
        duration + (end-start)*(1 if action == "duplicate" else -1))


def remove_time_ranges(plan, tracks, audio, ranges):
    if plan.status == "rendering":
        raise ValueError("正在渲染，不能修改时间线")
    duration = round(plan.duration_seconds*1000)
    cursor = at = 0
    intervals = []
    for start, end in ranges:
        if not (type(start) is int and type(end) is int and cursor <= start < end <= duration):
            raise ValueError("删除区间必须按时间排序、不重叠且位于成片内")
        if start > cursor:
            intervals.append((cursor, start, at))
            at += start-cursor
        cursor = end
    if cursor < duration:
        intervals.append((cursor, duration, at))
        at += duration-cursor
    if not ranges or at <= 0:
        raise ValueError("没有可删除的停顿，或删除后没有剩余内容")
    return _splice_plan(plan, tracks, audio, intervals, at)


def _splice_plan(plan, tracks, audio, intervals, new_duration_ms):
    result = creation_task_montage_plan_from_payload(creation_task_montage_plan_to_payload(plan))
    clips = []
    for clip in result.clips:
        clip_start, clip_end = round(clip.timeline_start_seconds * 1000), round(clip.timeline_end_seconds * 1000)
        parts = list(_pieces(clip_start, clip_end, intervals))
        if clip.locked and parts != [(clip_start, clip_end, clip_start)]:
            raise ValueError("关联编辑会改变已锁定镜头，请先解锁")
        for index, (left, right, at) in enumerate(parts):
            clips.append(replace(clip,
                id=clip.id if index == 0 else f"clip_{uuid4().hex[:12]}",
                source_start_seconds=round(clip.source_start_seconds + (left - clip_start) / 1000 * clip_playback_rate(clip), 3),
                source_end_seconds=round(clip.source_start_seconds + (right - clip_start) / 1000 * clip_playback_rate(clip), 3),
                transition_in_seconds=clip.transition_in_seconds if left == clip_start else 0,
                fade_envelope_offset_frames=clip.fade_envelope_offset_frames + (left-clip_start)/1000*30*clip.fade_envelope_rate,
                fade_envelope_duration_frames=(clip.fade_envelope_duration_frames or round(clip.timeline_end_seconds*30)-round(clip.timeline_start_seconds*30))
                    if clip.visual_effects.get("fade_in_seconds") or clip.visual_effects.get("fade_out_seconds") else clip.fade_envelope_duration_frames,
                timeline_start_seconds=at / 1000,
                timeline_end_seconds=(at + right - left) / 1000))
    result.clips = sorted(clips, key=lambda clip: clip.timeline_start_seconds)
    result.duration_seconds = new_duration_ms / 1000
    result.graphic_overlays = splice_overlays(plan.graphic_overlays,intervals)
    result.audio_timeline = splice_audio(audio, intervals)
    result.output_path = result.subtitle_path = None
    result.status = "ready"
    return result, splice_subtitles(tracks, intervals, result.duration_seconds)


def retime_clip(plan, tracks, audio, clip_id: str, rate: float, stretch_audio):
    """Scale a shot's occupied interval and every linked lane with one clock.

    Audio stretching creates derived sources via the caller's task artifact store;
    the original source and each independently editable track remain intact.
    """
    import math
    from app.audio_timeline import AudioGainPoint
    if not math.isfinite(rate) or not .25 <= rate <= 4 or rate == 1:
        raise ValueError("关联倍速须为0.25–4且不同于1")
    if plan.status == "rendering":
        raise ValueError("正在渲染，不能修改时间线")
    selected = next((clip for clip in plan.clips if clip.id == clip_id), None)
    if selected is None:
        raise ValueError("所选镜头不存在")
    start, end = round(selected.timeline_start_seconds * 1000), round(selected.timeline_end_seconds * 1000)
    new_length = round((end - start) / rate)
    if new_length < 40:
        raise ValueError("变速后镜头至少保留0.04秒")
    delta = new_length - (end - start)
    def move(time):
        if time <= start:
            return time
        if time >= end:
            return time + delta
        return start + round((time - start) * new_length / (end - start))
    # Check all locks before executing any media transform.
    for clip in plan.clips:
        a, b = round(clip.timeline_start_seconds * 1000), round(clip.timeline_end_seconds * 1000)
        if clip.locked and (move(a), move(b)) != (a, b):
            raise ValueError("关联变速会改变已锁定镜头，请先解锁")
    for track in audio.tracks:
        for clip in track.clips:
            a, b = clip.timeline_start_ms, clip.timeline_start_ms + clip.duration_ms
            if (track.locked or clip.locked) and (move(a), move(b)) != (a, b):
                raise ValueError("关联变速会改变已锁定音轨或音频片段，请先解锁")
    result = creation_task_montage_plan_from_payload(creation_task_montage_plan_to_payload(plan))
    for clip in result.clips:
        if clip.id == clip_id:
            scale = new_length / (end - start)
            if clip.visual_effects.get("fade_in_seconds") or clip.visual_effects.get("fade_out_seconds"):
                if clip.fade_envelope_duration_frames is None:
                    clip.fade_envelope_duration_frames = round(clip.timeline_end_seconds*30)-round(clip.timeline_start_seconds*30)
                clip.fade_envelope_rate /= scale
        clip.timeline_start_seconds = move(round(clip.timeline_start_seconds * 1000)) / 1000
        clip.timeline_end_seconds = move(round(clip.timeline_end_seconds * 1000)) / 1000
    result.graphic_overlays = [{**item,"start_seconds":move(round(item["start_seconds"]*1000))/1000,
        "end_seconds":move(round(item["end_seconds"]*1000))/1000} for item in result.graphic_overlays]
    result.duration_seconds = (round(plan.duration_seconds * 1000) + delta) / 1000
    result.audio_timeline = audio.model_copy(deep=True)
    spans = [(0, start, 0), (start, end, start), (end, round(plan.duration_seconds * 1000), end)]
    for track in result.audio_timeline.tracks:
        pieces = []
        for clip in track.clips:
            a, b = clip.timeline_start_ms, clip.timeline_start_ms + clip.duration_ms
            for index, (left, right, _) in enumerate(_pieces(a, b, spans)):
                partial = TimelineAudioClip.model_validate({**clip.model_dump(),
                    "id": clip.id if index == 0 else f"audio-{uuid4().hex[:16]}",
                    "source_start_ms": clip.source_start_ms + left - a,
                    "source_end_ms": clip.source_start_ms + right - a,
                    "timeline_start_ms": move(left),
                    "envelope_offset_ms": clip.envelope_offset_ms + left - a,
                    "envelope_duration_ms": clip.envelope_duration_ms or clip.duration_ms})
                if start <= left < end:
                    duration = move(right) - move(left)
                    factor = duration / (right - left)
                    # Map envelope coordinates, including partial fades, onto the derived source.
                    offset = round(partial.envelope_offset_ms * factor)
                    envelope_duration = max(offset + duration, round(partial.envelope_duration_ms * factor))
                    points = {round(point.offset_ms * factor): point.gain_db for point in partial.gain_points}
                    fade_in = round(partial.fade_in_ms * factor)
                    fade_out = min(round(partial.fade_out_ms * factor), envelope_duration - fade_in)
                    partial = TimelineAudioClip.model_validate({**partial.model_dump(),
                        "source_ref": stretch_audio(partial, rate, duration), "source_start_ms": 0, "source_end_ms": duration,
                        "envelope_offset_ms": offset, "envelope_duration_ms": envelope_duration,
                        "fade_in_ms": fade_in, "fade_out_ms": fade_out,
                        "gain_points": [AudioGainPoint(offset_ms=at, gain_db=gain) for at, gain in points.items()]})
                pieces.append(partial)
        track.clips = pieces
    result.audio_timeline = AudioTimeline.model_validate(result.audio_timeline.model_dump())
    result.output_path = result.subtitle_path = None
    result.status = "ready"
    subtitles = []
    for track in tracks:
        cues = [replace(cue, start_seconds=move(round(cue.start_seconds * 1000)) / 1000,
            end_seconds=move(round(cue.end_seconds * 1000)) / 1000) for cue in track.cues]
        moved = replace(track, cues=cues, artifact_ref=None, transform_id=None)
        subtitle_schedule([replace(moved, status="valid", style={**moved.style, "enabled": True})], result.duration_seconds)
        subtitles.append(moved)
    return result, subtitles
