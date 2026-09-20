"""One interval map for ripple video edits, audio source ranges and captions."""
from dataclasses import replace
from uuid import uuid4

from app.audio_timeline import AudioTimeline, TimelineAudioClip
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.subtitle_schedule import subtitle_schedule


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
                source_start_seconds=round(clip.source_start_seconds + (left - clip_start) / 1000, 3),
                source_end_seconds=round(clip.source_start_seconds + (right - clip_start) / 1000, 3),
                timeline_start_seconds=at / 1000,
                timeline_end_seconds=(at + right - left) / 1000))
    result.clips = sorted(clips, key=lambda clip: clip.timeline_start_seconds)
    result.duration_seconds = (duration + (end - start) * (1 if action == "duplicate" else -1)) / 1000
    result.audio_timeline = splice_audio(audio, intervals)
    result.output_path = result.subtitle_path = None
    result.status = "ready"
    return result, splice_subtitles(tracks, intervals, result.duration_seconds)
