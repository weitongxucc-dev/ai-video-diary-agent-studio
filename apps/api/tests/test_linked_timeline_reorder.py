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
    return _build_linked_state(person_render_edit_state)


def _build_linked_state(state, *, zh_cues: list[SubtitleCue] | None = None,
        narration_track: dict | None = None, swapped: bool = False, material: bool = False,
        plain: bool = False):
    """Two 4s presenter clips over one narration source.

    ``swapped`` lays the clips out in the reverse order (and the narration segments with them),
    which is the review's repro for "the reported old position must be the real one".
    ``material`` drops ``person_render_id`` from the second clip so the plan is mixed.
    ``plain`` leaves the plan without any audio track of its own, as before the first split.
    """
    plan = state.montage
    section_id = plan.cues[0].source_section_id
    segment_id = plan.cues[0].narration_segment_id
    whole = {"id": "narration-main", "name": "配音", "role": "narration", "clips": [
        {"id": "narration-whole", "name": "整片配音", "source_ref": plan.narration_audio_path,
         "source_start_ms": 0, "source_end_ms": 8000, "timeline_start_ms": 0}]}
    audio = AudioTimeline.model_validate({"tracks": [
        narration_track or whole,
        {"id": "music-main", "name": "背景音乐", "role": "music", "clips": [
            {"id": "audio-music-main", "name": "背景音乐", "source_ref": "artifacts/music.mp3",
             "source_start_ms": 0, "source_end_ms": 8000, "timeline_start_ms": 0}]},
    ]})
    # Both clips cut the same first 4s out of their own segment; only their timeline order differs,
    # which is what makes them a clean pair of equal-length sources to permute.
    clips = {}
    for index, (key, cue_id, start) in enumerate((("a", "cue-link-a", 4), ("b", "cue-link-b", 0)) if swapped
                                                 else (("a", "cue-link-a", 0), ("b", "cue-link-b", 4))):
        clips[key] = MontageClip(
            id=f"clip-link-{key}", cue_id=cue_id, asset_id=plan.clips[0].asset_id,
            segment_id=plan.clips[index].segment_id, source_path=plan.clips[0].source_path,
            source_start_seconds=0, source_end_seconds=4,
            timeline_start_seconds=start, timeline_end_seconds=start + 4,
            match_score=1, match_reasons=["已生成人物口型"], needs_confirmation=False,
            role="a_roll", manually_selected=True,
            person_render_id=state.old_render.id if not (material and key == "b") else None)
    linked = replace(
        plan,
        cues=[
            NarrationCue(id="cue-link-a", source_section_id=section_id, text="第一句。",
                duration_seconds=4, narration_segment_id=segment_id),
            NarrationCue(id="cue-link-b", source_section_id=section_id, text="第二句。",
                duration_seconds=4, narration_segment_id="narration-person-link-b"),
        ],
        clips=[clips["a"], clips["b"]],
        audio_timeline=None if plain else audio,
        narration_linkage_created_audio_timeline=False,
    )
    if swapped:
        linked = replace(linked, clips=[clips["b"], clips["a"]])
    a_start = 0 if not swapped else 4
    order = sorted(("a", "b"), key=lambda key: a_start if key == "a" else 4 - a_start)
    state.production.save_creation_task_montage_plan(
        state.workspace_id, state.task.id, linked.id, creation_task_montage_plan_to_payload(linked))
    version = main_module._ensure_montage_base_version(
        state.workspace_id, state.production.get_creation_task(state.workspace_id, state.task.id), linked)
    start_of = {"a": a_start, "b": 4 - a_start}
    text_of = {"a": "第一句。", "b": "第二句。"}
    state.production.create_subtitle_track(SubtitleTrack(
        id="link-zh", workspace_id=state.workspace_id, task_id=state.task.id,
        timeline_version_id=version.id, language="zh-CN", source_language=None,
        style={"font_size": 40},
        cues=zh_cues if zh_cues is not None else [
            SubtitleCue(id=f"cue-link-{key}", source_cue_id=None, text=text_of[key],
                start_seconds=start_of[key], end_seconds=start_of[key] + 4) for key in order],
        generation_method="timeline", model_id=None,
        artifact_ref="artifacts/link-zh.srt", transform_id="link-zh-transform", status="valid"))
    english = {"a": "First.", "b": "Second."}
    state.production.create_subtitle_track(SubtitleTrack(
        id="link-en", workspace_id=state.workspace_id, task_id=state.task.id,
        timeline_version_id=version.id, language="en-US", source_language="zh-CN",
        style={"font_size": 40},
        cues=[SubtitleCue(id=f"en-link-{key}", source_cue_id=f"cue-link-{key}", text=english[key],
                start_seconds=start_of[key], end_seconds=start_of[key] + 4) for key in order],
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


def test_material_clip_reorder_leaves_narration_and_subtitles_untouched(person_render_edit_state):
    """The mixed plan: the plan holds a person shot, but the shot being reordered is material."""
    state = _build_linked_state(person_render_edit_state, material=True)
    base = f"/api/creation-tasks/{state.task.id}"
    before = state.client.get(base).json()
    assert any(clip["person_render_id"] for clip in _plan(before)["clips"])

    preview = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "ReorderClip", "clip_id": "clip-link-b", "position": 0})
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    assert "配音与字幕已按来源一并移动" not in proposal["summary"]
    # The frozen picture-only default is stated against the moved object, not hidden as metadata.
    assert "本次移动的是纯素材镜头" in proposal["summary"]

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


def _split_narration_track(source_ref: str, *, a_start_ms: int = 0, b_start_ms: int = 4000,
        locked: bool = False, locked_clip_b: bool = False) -> dict:
    """An already-split narration group whose two segments carry different per-segment settings."""
    return {"id": "narration-main", "name": "配音", "role": "narration", "locked": locked, "clips": [
        {"id": "audio-narration-cue-link-a", "name": "第一句配音", "gain_db": -2,
         "source_ref": source_ref, "source_start_ms": 0, "source_end_ms": 4000,
         "timeline_start_ms": a_start_ms},
        {"id": "audio-narration-cue-link-b", "name": "第二句配音", "gain_db": -8,
         "locked": locked_clip_b,
         "source_ref": source_ref, "source_start_ms": 4000, "source_end_ms": 8000,
         "timeline_start_ms": b_start_ms}]}


def _reorder(state, clip_id: str, position: int):
    base = f"/api/creation-tasks/{state.task.id}"
    preview = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "ReorderClip", "clip_id": clip_id, "position": position})
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()["proposal"]["id"]
    assert state.client.post(base + f"/edits/{proposal_id}/confirm").status_code == 200
    return preview.json()["proposal"]


def test_second_reorder_keeps_each_split_segment_settings_and_reports_real_positions(person_render_edit_state):
    """Reorder an already-split group back: every per-segment setting must survive."""
    state = _build_linked_state(person_render_edit_state, swapped=True,
        narration_track=_split_narration_track(
            person_render_edit_state.montage.narration_audio_path, a_start_ms=4000, b_start_ms=0))
    base = f"/api/creation-tasks/{state.task.id}"

    preview = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "ReorderClip", "clip_id": "clip-link-a", "position": 0})
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    detail = proposal["preview"]["after"]["detail"]
    # a really moves 4.0s -> 0.0s. Its source offset is 0, so reporting "0.0 秒 → 0.0 秒" here
    # would mean the old position was read off the source offset instead of the timeline.
    assert "audio-narration-cue-link-a" in detail
    assert "时间线 4.0 秒 → 0.0 秒" in detail
    assert "时间线 4.0 秒 → 0.0 秒" not in detail.split("audio-narration-cue-link-a")[0]
    assert state.client.post(base + f"/edits/{proposal['id']}/confirm").status_code == 200

    narration = {clip["id"]: clip for clip in _audio_track(state.client.get(base).json(), "narration")["clips"]}
    assert (narration["audio-narration-cue-link-a"]["timeline_start_ms"],
            narration["audio-narration-cue-link-b"]["timeline_start_ms"]) == (0, 4000)
    # Neither segment is rebuilt from the other or from the old whole clip.
    assert (narration["audio-narration-cue-link-a"]["gain_db"],
            narration["audio-narration-cue-link-b"]["gain_db"]) == (-2, -8)
    assert (narration["audio-narration-cue-link-a"]["name"],
            narration["audio-narration-cue-link-b"]["name"]) == ("第一句配音", "第二句配音")
    assert (narration["audio-narration-cue-link-a"]["source_start_ms"],
            narration["audio-narration-cue-link-b"]["source_start_ms"]) == (0, 4000)
    zh = {cue["id"]: cue for cue in _cues(state.client.get(base).json(), "zh-CN")}
    assert (zh["cue-link-a"]["start_seconds"], zh["cue-link-a"]["end_seconds"]) == (0, 4)


@pytest.mark.parametrize("track_kwargs", [{"locked": True}, {"locked_clip_b": True}])
def test_locked_narration_track_or_segment_rejects_the_move(person_render_edit_state, track_kwargs):
    state = _build_linked_state(person_render_edit_state, narration_track=_split_narration_track(
        person_render_edit_state.montage.narration_audio_path, **track_kwargs))
    base = f"/api/creation-tasks/{state.task.id}"
    rejected = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "ReorderClip", "clip_id": "clip-link-a", "position": 1})
    assert rejected.status_code == 409, rejected.text
    assert "请先解锁" in rejected.json()["detail"]


def test_subtitle_keeps_its_relative_offset_inside_the_moved_source_group(person_render_edit_state):
    """A caption retimed inside its own group (0.5-3.5s) moves to 4.5-7.5, not to the full 4-8."""
    state = _build_linked_state(person_render_edit_state, zh_cues=[
        SubtitleCue(id="cue-link-a", source_cue_id=None, text="第一句。",
            start_seconds=0.5, end_seconds=3.5),
        SubtitleCue(id="cue-link-b", source_cue_id=None, text="第二句。",
            start_seconds=4.5, end_seconds=7.5)])
    _reorder(state, "clip-link-a", 1)

    zh = {cue["id"]: cue for cue in _cues(state.client.get(f"/api/creation-tasks/{state.task.id}").json(), "zh-CN")}
    assert (zh["cue-link-a"]["start_seconds"], zh["cue-link-a"]["end_seconds"]) == (4.5, 7.5)
    assert (zh["cue-link-b"]["start_seconds"], zh["cue-link-b"]["end_seconds"]) == (0.5, 3.5)


def _linkage_flag(state) -> bool:
    plan = creation_task_montage_plan_from_payload(
        state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id))
    return plan.narration_linkage_created_audio_timeline


@pytest.mark.parametrize("scenario,expected", [
    # A plan that already carried custom audio tracks stays on the pre-existing omission.
    ("already_custom", False),
    # The fixture's whole narration clip: this reorder is the plan's first split, and the plan was
    # on the whole-mix loudnorm before it, so it must still be on it afterwards.
    ("first_split", True),
    # A second reorder of a plan whose group is already split must not flip the choice back.
    ("second_reorder", False),
])
def test_loudness_strategy_is_kept_per_plan_state_instead_of_always_reapplied(
        person_render_edit_state, scenario, expected):
    source_ref = person_render_edit_state.montage.narration_audio_path
    if scenario == "already_custom":
        state = _build_linked_state(person_render_edit_state,
            narration_track=_split_narration_track(source_ref))
        _reorder(state, "clip-link-a", 1)
    elif scenario == "first_split":
        state = _build_linked_state(person_render_edit_state, plain=True)
        _reorder(state, "clip-link-a", 1)
    else:
        state = _build_linked_state(person_render_edit_state, swapped=True,
            narration_track=_split_narration_track(source_ref, a_start_ms=4000, b_start_ms=0))
        _reorder(state, "clip-link-a", 0)
        _reorder(state, "clip-link-a", 1)
    assert _linkage_flag(state) is expected
