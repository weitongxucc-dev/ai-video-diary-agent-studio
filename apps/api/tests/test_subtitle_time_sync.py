"""Regression coverage for Chinese cue timing edits with linked translations."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

import app.main as main_module
from app.compound_edits import apply_compound_subtitles
from app.creation.contracts import SubtitleCue, SubtitleTrack
from test_montage_pipeline import person_render_edit_state as person_render_edit_state


def tracks(*, references=("zh-1", "zh-2")):
    source = SubtitleTrack(
        id="zh", workspace_id="workspace", task_id="task", timeline_version_id="v1",
        language="zh-CN", source_language=None, style={"enabled": False},
        cues=[SubtitleCue(id="zh-1", source_cue_id=None, text="第一句", start_seconds=0, end_seconds=2),
              SubtitleCue(id="zh-2", source_cue_id=None, text="第二句", start_seconds=3, end_seconds=5)],
        generation_method="timeline", model_id=None, artifact_ref="zh.srt",
        transform_id="zh-transform", status="valid",
    )
    translation = replace(
        source, id="en", language="en-US", source_language="zh-CN",
        cues=[SubtitleCue(id=f"en-{index}", source_cue_id=reference, text=text,
                          start_seconds=start, end_seconds=end)
              for index, (reference, text, start, end) in enumerate(zip(
                  references, ("First sentence", "Second sentence"), (0, 3), (2, 5), strict=False))],
        generation_method="translation", artifact_ref="en.srt", transform_id="en-transform",
    )
    return source, translation


def edit(**values):
    return [{"operation_type": "UpdateSubtitleCue", "enabled": True,
             "values": {"language": "zh-CN", "cue_number": 1, **values}}]


def test_only_chinese_time_changes_sync_linked_english_and_preserve_text():
    source, translation = tracks()
    result = apply_compound_subtitles([source, translation], edit(end_seconds=1.8), 6)
    assert [(cue.start_seconds, cue.end_seconds, cue.text) for cue in result[0].cues] == [
        (0, 1.8, "第一句"), (3, 5, "第二句")]
    assert [(cue.start_seconds, cue.end_seconds, cue.text) for cue in result[1].cues] == [
        (0, 1.8, "First sentence"), (3, 5, "Second sentence")]
    assert result[0].style == source.style and result[1].style == translation.style
    assert result[0].artifact_ref is None and result[1].artifact_ref is None
    assert source.cues[0].end_seconds == translation.cues[0].end_seconds == 2


def test_chinese_time_edit_without_translation_succeeds():
    source, _ = tracks()
    result = apply_compound_subtitles([source], edit(end_seconds=1.8), 6)
    assert len(result) == 1 and result[0].cues[0].end_seconds == 1.8


@pytest.mark.parametrize("references", [(None, "zh-2"), ("missing", "zh-2"), ("zh-1", "zh-1")])
def test_missing_or_ambiguous_link_rejects_without_partial_write(references):
    source, translation = tracks(references=references)
    before = (source, translation)
    with pytest.raises(ValueError, match="对应关系"):
        apply_compound_subtitles([source, translation], edit(end_seconds=1.8), 6)
    assert (source, translation) == before
    assert source.cues[0].end_seconds == translation.cues[0].end_seconds == 2


def test_multiple_valid_translations_reject_without_guessing():
    source, translation = tracks()
    with pytest.raises(ValueError, match="关联"):
        apply_compound_subtitles([source, translation, replace(translation, id="en-other")],
                                 edit(end_seconds=1.8), 6)


def test_chinese_text_change_keeps_existing_translation_invalidation():
    source, translation = tracks()
    result = apply_compound_subtitles([source, translation], edit(text="改过的第一句"), 6)
    assert [track.language for track in result] == ["zh-CN"]


@pytest.mark.parametrize("english_source_language", ["zh-CN", None])
def test_exact_chinese_text_change_preserves_all_english_invalidation(
        person_render_edit_state, monkeypatch, english_source_language):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    version = main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    source, translation = tracks()
    english = replace(translation, source_language=english_source_language,
                      generation_method="translation" if english_source_language else "timeline")
    for track in (source, english):
        state.production.create_subtitle_track(replace(
            track, workspace_id=state.workspace_id, task_id=state.task.id,
            timeline_version_id=version.id, style={"enabled": True}))
    before = state.client.get(base).json()
    monkeypatch.setattr(main_module, "_task_audio", lambda: SimpleNamespace(validate=lambda *args, draft_only=False: None))
    monkeypatch.setattr(main_module, "_publish_task_picture_preview", lambda *args, **kwargs:
                        SimpleNamespace(id="text-preview", logical_ref="artifacts/text-preview.mp4"))
    preview = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "UpdateSubtitleCue", "track_id": source.id, "cue_id": source.cues[0].id,
        "text": "改过的第一句",
    })
    assert preview.status_code == 200, preview.text
    assert state.client.get(base).json()["subtitle_tracks"] == before["subtitle_tracks"]
    confirmed = state.client.post(base + "/edits/" + preview.json()["proposal"]["id"] + "/confirm")
    assert confirmed.status_code == 200, confirmed.text
    after = state.client.get(base).json()
    assert [track["language"] for track in after["subtitle_tracks"]] == ["zh-CN"]
    assert after["subtitle_tracks"][0]["cues"][0]["text"] == "改过的第一句"
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    restored = state.client.get(base).json()
    assert {track["language"]: track["cues"] for track in restored["subtitle_tracks"]} == {
        track["language"]: track["cues"] for track in before["subtitle_tracks"]}


@pytest.mark.parametrize("entry", ["compound", "exact"])
@pytest.mark.parametrize("start,end,expected", [
    (0, 1.8, ["1.80–2.00"]),
    (0.5, 1.8, ["0.00–0.50", "1.80–2.00"]),
    (0, 2.5, []),
])
def test_preview_reports_new_gaps_without_writing(person_render_edit_state, monkeypatch, entry, start, end, expected):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    version = main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    source, translation = tracks()
    for track in (source, translation):
        state.production.create_subtitle_track(replace(track, workspace_id=state.workspace_id,
            task_id=state.task.id, timeline_version_id=version.id, style={"enabled": True}))
    before = state.client.get(base).json()
    monkeypatch.setattr(main_module, "_task_audio", lambda: SimpleNamespace(validate=lambda *args, draft_only=False: None))
    monkeypatch.setattr(main_module, "_publish_task_picture_preview", lambda *args, **kwargs:
                        SimpleNamespace(id="gap-preview", logical_ref="artifacts/gap-preview.mp4"))
    preview = (state.client.post(base + "/edits/preview", json={
        "raw_command": f"中文字幕1显示{start}到{end}秒", "context_stage": "subtitle_cover",
        "selected_object_refs": [],
    }) if entry == "compound" else state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "UpdateSubtitleCue", "track_id": source.id, "cue_id": source.cues[0].id,
        "start_seconds": start, "end_seconds": end,
    }))
    assert preview.status_code == 200, preview.text
    gap_effects = [effect for effect in preview.json()["proposal"]["external_effects"] if "新增无字幕" in effect]
    assert sorted(gap_effects) == sorted(f"{language}字幕新增无字幕时段：{interval} 秒。"
                                       for language in ("中文", "英文") for interval in expected)
    assert state.client.get(base).json() == before


def test_compound_preview_reports_only_final_gaps(person_render_edit_state, monkeypatch):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    version = main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    source, translation = tracks()
    for track in (source, translation):
        state.production.create_subtitle_track(replace(track, workspace_id=state.workspace_id,
            task_id=state.task.id, timeline_version_id=version.id, style={"enabled": True}))
    before = state.client.get(base).json()
    monkeypatch.setattr(main_module, "_task_audio", lambda: SimpleNamespace(validate=lambda *args, draft_only=False: None))
    monkeypatch.setattr(main_module, "_publish_task_picture_preview", lambda *args, **kwargs:
                        SimpleNamespace(id="gap-preview", logical_ref="artifacts/gap-preview.mp4"))
    preview = state.client.post(base + "/edits/preview", json={
        "raw_command": "中文字幕1显示0到1.8秒，然后中文字幕2显示1.8到5秒",
        "context_stage": "subtitle_cover", "selected_object_refs": [],
    })
    assert preview.status_code == 200, preview.text
    assert not any("新增无字幕" in effect for effect in preview.json()["proposal"]["external_effects"])
    assert state.client.get(base).json() == before


@pytest.mark.parametrize("entry", ["compound", "exact"])
def test_confirm_syncs_both_tracks_exports_one_bilingual_srt_and_undoes(
        person_render_edit_state, monkeypatch, tmp_path, entry):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    version = main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    source, translation = tracks()
    source = replace(source, workspace_id=state.workspace_id, task_id=state.task.id,
                     timeline_version_id=version.id, style={"enabled": True})
    translation = replace(translation, workspace_id=state.workspace_id, task_id=state.task.id,
                          timeline_version_id=version.id, style={"enabled": True})
    state.production.create_subtitle_track(source)
    state.production.create_subtitle_track(translation)
    before = state.client.get(base).json()
    monkeypatch.setattr(main_module, "_task_audio", lambda: SimpleNamespace(validate=lambda *args, draft_only=False: None))
    monkeypatch.setattr(main_module, "_publish_task_picture_preview", lambda *args, **kwargs:
                        SimpleNamespace(id="timing-preview", logical_ref="artifacts/timing-preview.mp4"))
    preview = (state.client.post(base + "/edits/preview", json={
        "raw_command": "中文字幕1显示0到1.8秒", "context_stage": "subtitle_cover",
        "selected_object_refs": [],
    }) if entry == "compound" else state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "UpdateSubtitleCue", "track_id": source.id, "cue_id": source.cues[0].id,
        "text": source.cues[0].text, "end_seconds": 1.8,
    }))
    assert preview.status_code == 200, preview.text
    assert state.client.get(base).json()["subtitle_tracks"] == before["subtitle_tracks"]
    confirmed = state.client.post(base + "/edits/" + preview.json()["proposal"]["id"] + "/confirm")
    assert confirmed.status_code == 200, confirmed.text
    after = state.client.get(base).json()
    assert after["task"]["current_timeline_version_id"] != before["task"]["current_timeline_version_id"]
    assert {track["language"]: (track["cues"][0]["end_seconds"], track["cues"][0]["text"])
            for track in after["subtitle_tracks"]} == {
                "zh-CN": (1.8, "第一句"), "en-US": (1.8, "First sentence")}
    assert after["narration"]["audio_reference"] == before["narration"]["audio_reference"]
    selected = {track["language"]: track for track in after["subtitle_tracks"]}
    exported = state.client.get(base + "/output/selected-subtitles", params={
        "expected_version_id": after["task"]["current_timeline_version_id"],
        "mode": "zh-en", "zh_track_id": selected["zh-CN"]["id"],
        "en_track_id": selected["en-US"]["id"],
    })
    assert exported.status_code == 200, exported.text
    (tmp_path / "confirmed-bilingual.srt").write_text(exported.text, encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,800\n第一句\nFirst sentence" in exported.text
    assert "00:00:03,000 --> 00:00:05,000\n第二句\nSecond sentence" in exported.text
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    restored = state.client.get(base).json()
    for language in ("zh-CN", "en-US"):
        assert next(track["cues"] for track in restored["subtitle_tracks"] if track["language"] == language) == next(
            track["cues"] for track in before["subtitle_tracks"] if track["language"] == language)
    assert restored["narration"]["audio_reference"] == before["narration"]["audio_reference"]


@pytest.mark.parametrize("entry", ["compound", "exact"])
@pytest.mark.parametrize("references", [(None, "zh-2"), ("zh-1", "zh-1")])
def test_broken_link_rejects_preview_without_persisting_any_track(
        person_render_edit_state, monkeypatch, references, entry):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    version = main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    source, translation = tracks(references=references)
    for track in (source, translation):
        state.production.create_subtitle_track(replace(
            track, workspace_id=state.workspace_id, task_id=state.task.id,
            timeline_version_id=version.id, style={"enabled": True}))
    before = state.client.get(base).json()
    monkeypatch.setattr(main_module, "_task_audio", lambda: SimpleNamespace(validate=lambda *args, draft_only=False: None))
    rejected = (state.client.post(base + "/edits/preview", json={
        "raw_command": "中文字幕1显示0到1.8秒", "context_stage": "subtitle_cover",
        "selected_object_refs": [],
    }) if entry == "compound" else state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "UpdateSubtitleCue", "track_id": source.id, "cue_id": source.cues[0].id,
        "text": source.cues[0].text, "end_seconds": 1.8,
    }))
    assert rejected.status_code == 422, rejected.text
    after = state.client.get(base).json()
    assert after["task"]["current_timeline_version_id"] == before["task"]["current_timeline_version_id"]
    assert after["subtitle_tracks"] == before["subtitle_tracks"]


def test_bilingual_merge_publishes_both_tracks_exports_and_undoes(person_render_edit_state):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    version = main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    for track in tracks():
        state.production.create_subtitle_track(replace(track, workspace_id=state.workspace_id,
            task_id=state.task.id, timeline_version_id=version.id, style={"enabled": True}))
    before = state.client.get(base).json()
    preview = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "MergeSubtitleCue", "track_id": "zh", "cue_id": "zh-1"})
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    assert len(proposal["preview"]["text_changes"]) == 2
    assert "英文" in proposal["preview"]["text_changes"][1]["after"]
    assert state.client.get(base).json() == before
    confirmed = state.client.post(base + "/edits/" + proposal["id"] + "/confirm")
    assert confirmed.status_code == 200, confirmed.text
    after = state.client.get(base).json()
    selected = {track["language"]: track for track in after["subtitle_tracks"]}
    assert selected["zh-CN"]["cues"][0]["text"] == "第一句第二句"
    assert selected["en-US"]["cues"][0]["text"] == "First sentence Second sentence"
    assert selected["en-US"]["cues"][0]["source_cue_id"] == "zh-1"
    for track in selected.values():
        assert len(track["cues"]) == 1 and track["status"] == "valid"
        assert track["timeline_version_id"] == after["task"]["current_timeline_version_id"]
        assert (track["cues"][0]["start_seconds"], track["cues"][0]["end_seconds"]) == (0, 5)
        assert main_module.artifact_store.resolve_file(track["artifact_ref"]).read_text().strip()
        transform = state.production.lineage_records.get_transform_run(state.workspace_id, track["transform_id"])
        assert transform.status == "succeeded" and transform.output_refs == [track["artifact_ref"]]
    exported = state.client.get(base + "/output/selected-subtitles", params={
        "expected_version_id": after["task"]["current_timeline_version_id"], "mode": "zh-en",
        "zh_track_id": selected["zh-CN"]["id"], "en_track_id": selected["en-US"]["id"]})
    assert exported.status_code == 200, exported.text
    assert "第一句第二句\nFirst sentence Second sentence" in exported.text
    assert after["montage"]["narration_reference"] == before["montage"]["narration_reference"]
    assert after["montage"]["clips"] == before["montage"]["clips"]
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    restored = state.client.get(base).json()
    assert {t["language"]: t["cues"] for t in restored["subtitle_tracks"]} == {t["language"]: t["cues"] for t in before["subtitle_tracks"]}
    selected = {t["language"]: t for t in restored["subtitle_tracks"]}
    assert state.client.get(base + "/output/selected-subtitles", params={
        "expected_version_id": restored["task"]["current_timeline_version_id"], "mode": "zh-en",
        "zh_track_id": selected["zh-CN"]["id"], "en_track_id": selected["en-US"]["id"]}).status_code == 200


def test_bilingual_merge_rejects_missing_mapping_without_writing(person_render_edit_state):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    version = main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    for track in tracks(references=(None, "zh-2")):
        state.production.create_subtitle_track(replace(track, workspace_id=state.workspace_id,
            task_id=state.task.id, timeline_version_id=version.id, style={"enabled": True}))
    before = state.client.get(base).json()
    preview = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "MergeSubtitleCue", "track_id": "zh", "cue_id": "zh-1"})
    assert preview.status_code == 422 and "对应关系" in preview.text
    assert state.client.get(base).json() == before
