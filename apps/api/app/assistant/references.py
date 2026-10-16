"""Selection identity and timeline bounds; authorization stays with the caller.

All ranges are timeline milliseconds, never source-file offsets. The supplied
task, version and subtitle tracks must already be read through trusted access.
"""
from __future__ import annotations

from collections.abc import Sequence
from app.creation.contracts import CreationTask, SubtitleTrack, TimelineVersion


class AssistantReferenceConflict(ValueError):
    pass


def reference_identity(reference: dict) -> tuple:
    kind = reference["kind"]
    if kind == "material":
        item = reference["selection"]
        return kind, item["asset_id"], item.get("segment_id")
    if kind in {"person", "voice"}:
        return kind, reference["profile_id"]
    if kind == "knowledge":
        return kind, reference["knowledge_item_id"], reference.get("locator")
    return kind, reference["task_id"], reference["object_id"]


def normalize_selection(reference: dict | None) -> dict | None:
    """One saved JSON object, one CAS revision; no parallel reference store."""
    if reference is None or reference["kind"] != "selection":
        return reference
    items = reference["items"]
    if not 1 <= len(items) <= 32 or any(item["kind"] == "selection" for item in items):
        raise AssistantReferenceConflict("引用集合需要 1–32 个具体对象，不能嵌套。")
    unique = {}
    for item in items:
        identity = reference_identity(item)
        if identity in unique and unique[identity] != item:
            raise AssistantReferenceConflict("同一引用包含不同版本或范围，请重新选择。")
        unique[identity] = item
    return {"kind": "selection", "items": list(unique.values())}


def timeline_object_refs(items: Sequence[dict], task_id: str,
                         subtitle_tracks: Sequence[SubtitleTrack] = ()) -> list[str]:
    """Project already-authorized frozen selections into the edit tool contract."""
    refs: list[str] = []
    prefixes = {"shot": "clip", "effect": "overlay", "narration": "narration", "audio": "audio"}
    for item in items:
        kind = item["kind"]
        if kind not in {*prefixes, "subtitle"}:
            continue
        if item["task_id"] != task_id:
            raise AssistantReferenceConflict("选中引用属于另一任务，请重新选择；没有切换编辑目标。")
        object_id = item["object_id"]
        if kind in prefixes:
            refs.append(f"{prefixes[kind]}:{object_id}")
            continue
        track = next((track for track in subtitle_tracks if track.task_id == task_id
                      and track.timeline_version_id == item["version"] and track.status == "valid"
                      and (track.id == object_id or any(cue.id == object_id for cue in track.cues))), None)
        if track is None:
            raise AssistantReferenceConflict("此版本的字幕引用已不可用，请重新选择。")
        if track.id != object_id:
            refs.append(f"cue:{object_id}")
        refs.append(f"subtitle-track:{track.id}")
    return list(dict.fromkeys(refs))


def validate_timeline_reference(reference: dict, *, task: CreationTask,
                                timeline: TimelineVersion,
                                subtitle_tracks: Sequence[SubtitleTrack] = ()) -> dict:
    if (reference["task_id"] != task.id or timeline.task_id != task.id
            or timeline.workspace_id != task.workspace_id
            or reference["version"] != timeline.id
            or task.current_timeline_version_id != timeline.id):
        raise AssistantReferenceConflict("引用所属任务或版本已变化，请重新选择；没有改绑新版。")
    kind, object_id = reference["kind"], reference["object_id"]
    montage = timeline.snapshot.get("montage") or {}
    bounds: tuple[int, int] | None = None
    found = False
    if kind in {"shot", "effect"}:
        key = "clips" if kind == "shot" else "graphic_overlays"
        item = next((item for item in montage.get(key, []) if item["id"] == object_id), None)
        if item is not None:
            found = True
            prefix = "timeline_" if kind == "shot" else ""
            bounds = (round(item[f"{prefix}start_seconds"] * 1000), round(item[f"{prefix}end_seconds"] * 1000))
    elif kind == "narration":
        segments = timeline.snapshot.get("narration_segments", [])
        found = any(item["id"] == object_id and item["task_id"] == task.id
                    and item["workspace_id"] == task.workspace_id for item in segments)
        cursor = 0
        spans = []
        for cue in montage.get("cues", []):
            end = cursor + round(cue["duration_seconds"] * 1000)
            if cue.get("narration_segment_id") == object_id:
                spans.append((cursor, end))
            cursor = end
        if found and spans:
            bounds = (spans[0][0], spans[-1][1])
    elif kind == "audio":
        tracks = (montage.get("audio_timeline") or {}).get("tracks", [])
        for track in tracks:
            clips = track.get("clips", []) if track["id"] == object_id else [item for item in track.get("clips", []) if item["id"] == object_id]
            if clips:
                found = True
                bounds = (min(item["timeline_start_ms"] for item in clips),
                          max(item["timeline_start_ms"] + round((item["source_end_ms"] - item["source_start_ms"]) / item.get("playback_rate", 1)) for item in clips))
                break
    elif kind == "subtitle":
        for track in subtitle_tracks:
            if (track.task_id != task.id or track.workspace_id != task.workspace_id
                    or track.timeline_version_id != timeline.id or track.status != "valid"):
                continue
            cues = track.cues if track.id == object_id else [cue for cue in track.cues if cue.id == object_id]
            if cues:
                found = True
                bounds = (round(min(cue.start_seconds for cue in cues) * 1000), round(max(cue.end_seconds for cue in cues) * 1000))
                break
    if not found:
        raise AssistantReferenceConflict("此版本中没有可引用的该对象，请重新选择。")
    start, end = reference.get("start_ms"), reference.get("end_ms")
    if (start is None) != (end is None):
        raise AssistantReferenceConflict("引用范围须同时包含开始与结束时间。")
    if start is not None and (type(start) is not int or type(end) is not int or bounds is None
                              or not bounds[0] <= start < end <= bounds[1]):
        raise AssistantReferenceConflict("引用范围超出当前对象的时间线范围，请重新选择。")
    return dict(reference)
