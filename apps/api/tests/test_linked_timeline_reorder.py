from __future__ import annotations

from dataclasses import replace

import pytest

import app.main as main_module
from app.audio_timeline import AudioTimeline
from app.montage import MontageClip, NarrationCue, creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.production import SubtitleCue, SubtitleTrack
from test_montage_pipeline import person_render_edit_state  # noqa: F401


def _plan(workspace: dict) -> dict:
    return workspace["montage"]


def _audio_track(workspace: dict, role: str) -> dict:
    return next(track for track in _plan(workspace)["audio_timeline"]["tracks"] if track["role"] == role)


def _cues(workspace: dict, language: str) -> list[dict]:
    return next(track for track in workspace["subtitle_tracks"] if track["language"] == language)["cues"]


@pytest.fixture
def linked_reorder_state(person_render_edit_state):
    """Two distinct narration sources behind two presenter clips, plus an untouched music lane."""
    state = person_render_edit_state
    plan = state.montage
    section_id = plan.cues[0].source_section_id
    segment_id = plan.cues[0].narration_segment_id
    audio = AudioTimeline.model_validate({"tracks": [
        {"id": "narration-main", "name": "配音", "role": "narration", "clips": [
            {"id": "narration-whole", "name": "整片配音", "source_ref": plan.narration_audio_path,
             "source_start_ms": 0, "source_end_ms": 8000, "timeline_start_ms": 0}]},
        {"id": "music-main", "name": "背景音乐", "role": "music", "clips": [
            {"id": "audio-music-main", "name": "背景音乐", "source_ref": "artifacts/music.mp3",
             "source_start_ms": 0, "source_end_ms": 8000, "timeline_start_ms": 0}]},
    ]})
    linked = replace(
        plan,
        cues=[
            NarrationCue(id="cue-link-a", source_section_id=section_id, text="第一句。",
                duration_seconds=4, narration_segment_id=segment_id),
            NarrationCue(id="cue-link-b", source_section_id=section_id, text="第二句。",
                duration_seconds=4, narration_segment_id="narration-person-link-b"),
        ],
        clips=[
            MontageClip(id="clip-link-a", cue_id="cue-link-a", asset_id=plan.clips[0].asset_id,
                segment_id=plan.clips[0].segment_id, source_path=plan.clips[0].source_path,
                source_start_seconds=0, source_end_seconds=4,
                timeline_start_seconds=0, timeline_end_seconds=4,
                match_score=1, match_reasons=["已生成人物口型"], needs_confirmation=False,
                role="a_roll", manually_selected=True, person_render_id=state.old_render.id),
            MontageClip(id="clip-link-b", cue_id="cue-link-b", asset_id=plan.clips[0].asset_id,
                segment_id=plan.clips[1].segment_id, source_path=plan.clips[0].source_path,
                source_start_seconds=4, source_end_seconds=8,
                timeline_start_seconds=4, timeline_end_seconds=8,
                match_score=1, match_reasons=["已生成人物口型"], needs_confirmation=False,
                role="a_roll", manually_selected=True, person_render_id=state.old_render.id),
        ],
        audio_timeline=audio,
        narration_linkage_reordered=False,
    )
    state.production.save_creation_task_montage_plan(
        state.workspace_id, state.task.id, linked.id, creation_task_montage_plan_to_payload(linked))
    version = main_module._ensure_montage_base_version(
        state.workspace_id, state.production.get_creation_task(state.workspace_id, state.task.id), linked)
    state.production.create_subtitle_track(SubtitleTrack(
        id="link-zh", workspace_id=state.workspace_id, task_id=state.task.id,
        timeline_version_id=version.id, language="zh-CN", source_language=None,
        style={"font_size": 40},
        cues=[SubtitleCue(id="cue-link-a", source_cue_id=None, text="第一句。", start_seconds=0, end_seconds=4),
              SubtitleCue(id="cue-link-b", source_cue_id=None, text="第二句。", start_seconds=4, end_seconds=8)],
        generation_method="timeline", model_id=None,
        artifact_ref="artifacts/link-zh.srt", transform_id="link-zh-transform", status="valid"))
    state.production.create_subtitle_track(SubtitleTrack(
        id="link-en", workspace_id=state.workspace_id, task_id=state.task.id,
        timeline_version_id=version.id, language="en-US", source_language="zh-CN",
        style={"font_size": 40},
        cues=[SubtitleCue(id="en-link-a", source_cue_id="cue-link-a", text="First.", start_seconds=0, end_seconds=4),
              SubtitleCue(id="en-link-b", source_cue_id="cue-link-b", text="Second.", start_seconds=4, end_seconds=8)],
        generation_method="translation", model_id=None,
        artifact_ref="artifacts/link-en.srt", transform_id="link-en-transform", status="valid"))
    return state


def test_person_clip_reorder_moves_narration_and_subtitles_as_one_source_group(linked_reorder_state):
    state = linked_reorder_state
    base = f"/api/creation-tasks/{state.task.id}"
    before = state.client.get(base).json()
    music_before = _audio_track(before, "music")

    preview = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "ReorderClip", "clip_id": "clip-link-a", "position": 1})
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    # The proposal has to name the linked objects and the old -> new position in the visible text.
    assert "配音与字幕已按来源一并移动" in proposal["summary"]
    detail = proposal["preview"]["after"]["detail"]
    assert "audio-narration-cue-link-a" in detail
    assert "narration://artifact/person-segment-cache" in detail
    assert "0–4000 毫秒保持不变" in detail
    assert "时间线 0.0 秒 → 4.0 秒" in detail
    assert "zh-CN：cue-link-a" in detail and "en-US：en-link-a" in detail
    assert state.client.get(base).json()["montage"] == before["montage"]

    confirmed = state.client.post(base + f"/edits/{proposal['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    after = state.client.get(base).json()

    narration = {clip["id"]: clip for clip in _audio_track(after, "narration")["clips"]}
    assert set(narration) == {"audio-narration-cue-link-a", "audio-narration-cue-link-b"}
    moved, kept = narration["audio-narration-cue-link-a"], narration["audio-narration-cue-link-b"]
    # Source identity is untouched; only the timeline position follows the clip.
    assert (moved["source_ref"], moved["source_start_ms"], moved["source_end_ms"]) == (
        "narration://artifact/person-segment-cache", 0, 4000)
    assert (kept["source_start_ms"], kept["source_end_ms"]) == (4000, 8000)
    assert (moved["timeline_start_ms"], kept["timeline_start_ms"]) == (4000, 0)
    assert _audio_track(after, "music") == music_before
    assert [clip["id"] for clip in _plan(after)["clips"]] == ["clip-link-b", "clip-link-a"]

    zh = {cue["id"]: cue for cue in _cues(after, "zh-CN")}
    en = {cue["id"]: cue for cue in _cues(after, "en-US")}
    assert (zh["cue-link-a"]["start_seconds"], zh["cue-link-a"]["end_seconds"]) == (4, 8)
    assert zh["cue-link-a"]["source_cue_id"] is None
    assert (en["en-link-a"]["start_seconds"], en["en-link-a"]["end_seconds"]) == (4, 8)
    assert en["en-link-a"]["source_cue_id"] == "cue-link-a"
    assert next(track for track in after["subtitle_tracks"] if track["language"] == "zh-CN")["artifact_ref"] != (
        "artifacts/link-zh.srt")

    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    restored = state.client.get(base).json()
    assert _plan(restored)["clips"] == _plan(before)["clips"]
    assert _plan(restored)["audio_timeline"] == _plan(before)["audio_timeline"]
    assert _cues(restored, "zh-CN") == _cues(before, "zh-CN")
    assert _cues(restored, "en-US") == _cues(before, "en-US")

    assert state.client.post(base + "/timeline/redo", json={}).status_code == 200
    redone = state.client.get(base).json()
    assert _plan(redone)["audio_timeline"] == _plan(after)["audio_timeline"]
    assert _cues(redone, "zh-CN") == _cues(after, "zh-CN")
    assert _cues(redone, "en-US") == _cues(after, "en-US")


def test_material_clip_reorder_leaves_narration_and_subtitles_untouched(linked_reorder_state):
    state = linked_reorder_state
    plan = creation_task_montage_plan_from_payload(
        state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id))
    material = replace(plan, clips=[replace(item, person_render_id=None) for item in plan.clips])
    state.production.save_creation_task_montage_plan(
        state.workspace_id, state.task.id, plan.id, creation_task_montage_plan_to_payload(material))
    base = f"/api/creation-tasks/{state.task.id}"
    before = state.client.get(base).json()

    preview = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "ReorderClip", "clip_id": "clip-link-a", "position": 1})
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    assert "配音与字幕已按来源一并移动" not in proposal["summary"]
    assert proposal["summary"].endswith("文案、配音和其他镜头保持不变。")

    assert state.client.post(base + f"/edits/{proposal['id']}/confirm").status_code == 200
    after = state.client.get(base).json()
    assert [clip["id"] for clip in _plan(after)["clips"]] == ["clip-link-b", "clip-link-a"]
    assert _plan(after)["audio_timeline"] == _plan(before)["audio_timeline"]
    assert _cues(after, "zh-CN") == _cues(before, "zh-CN")
    assert _cues(after, "en-US") == _cues(before, "en-US")

    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    restored = state.client.get(base).json()
    assert [clip["id"] for clip in _plan(restored)["clips"]] == ["clip-link-a", "clip-link-b"]
    assert _plan(restored)["audio_timeline"] == _plan(before)["audio_timeline"]
