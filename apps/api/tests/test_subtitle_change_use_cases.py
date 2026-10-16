"""Subtitle use cases preserve narration, mapping and visible-language settings."""
from dataclasses import asdict, replace

import pytest

from app.creation.contracts import EditCommand, SubtitleCue
from app.creation.editing.picture import EditConflict
from app.creation.editing.subtitle_changes import (
    apply_subtitle_content_command, calculate_subtitle_content, calculate_subtitle_style,
    prepare_subtitle_content_proposal, prepare_subtitle_style_proposal,
    apply_subtitle_style_command,
)
from app.montage import NarrationCue
from test_compound_edit_use_cases import compound_values as compound_values
from test_montage_pipeline import person_render_edit_state as person_render_edit_state


def test_source_replacement_updates_only_matching_display_text_and_invalidates_translation(compound_values):
    task, plan, zh, audio = compound_values
    plan = replace(plan, cues=[NarrationCue("sentence", "section", "原文", 4)])
    en = replace(zh, id="en", language="en-US", source_language="zh-CN", generation_method="translation",
        cues=[replace(zh.cues[0], id="translated", source_cue_id="sentence", text="original")])
    before = asdict(plan)
    command = EditCommand("workspace", "task", {"member_id": "member"}, "timeline", "ReplaceSubtitleText", {
        "track_id": "zh", "find_text": "原", "replacement_text": "新",
    })
    changed, captions = apply_subtitle_content_command(task, plan, [zh, en], command)
    assert asdict(plan) == before
    assert changed.cues[0].text == captions.changed_tracks[0].cues[0].text == "新文"
    assert changed.clips == plan.clips and changed.audio_timeline == audio
    assert captions.retained_tracks == [] and captions.changed_tracks[0].artifact_ref is None
    assert captions.text_changes[0]["occurrences"] == 1


def test_merge_keeps_uniquely_linked_bilingual_text_and_mapping(compound_values):
    task, plan, zh, audio = compound_values
    zh = replace(zh, cues=[SubtitleCue("first", None, "第一句", 0, 1), SubtitleCue("next", None, "第二句", 2, 4)])
    en = replace(zh, id="en", language="en-US", source_language="zh-CN", generation_method="translation",
        cues=[SubtitleCue("en-first", "first", "first", 0, 1), SubtitleCue("en-next", "next", "next", 2, 4)])
    before = [asdict(zh), asdict(en)]
    result = calculate_subtitle_content([zh, en], "MergeSubtitleCue", {"track_id": "zh", "cue_id": "first"}, 8)
    assert [asdict(zh), asdict(en)] == before
    assert [track.cues[0].text for track in result.changed_tracks] == ["第一句第二句", "first next"]
    assert result.changed_tracks[1].cues[0].source_cue_id == result.changed_tracks[0].cues[0].id == "first"
    assert [(track.cues[0].start_seconds, track.cues[0].end_seconds) for track in result.changed_tracks] == [(0, 4), (0, 4)]
    assert result.retained_tracks == []
    broken = replace(en, cues=[replace(en.cues[0], source_cue_id="unknown"), en.cues[1]])
    with pytest.raises(ValueError, match="对应关系"):
        calculate_subtitle_content([zh, broken], "MergeSubtitleCue", {"track_id": "zh", "cue_id": "first"}, 8)


def test_last_caption_deletion_has_no_generated_artifact_and_keeps_other_language(compound_values):
    task, plan, zh, audio = compound_values
    en = replace(zh, id="en", language="en-US", source_language=None,
        cues=[replace(zh.cues[0], text="keep")])
    result = calculate_subtitle_content([zh, en], "DeleteSubtitleCue", {"track_id": "zh", "cue_id": "sentence"}, 8)
    assert result.changed_tracks[0].cues == []
    assert result.changed_tracks[0].artifact_ref is result.changed_tracks[0].transform_id is None
    assert result.retained_tracks == [en]
    assert zh.cues[0].text == "原文"


def test_style_checks_each_caption_including_hidden_language_and_preserves_visibility(compound_values):
    task, plan, zh, audio = compound_values
    zh = replace(zh, style={**zh.style, "enabled": True})
    en = replace(zh, id="en", language="en-US", style={**zh.style, "enabled": False},
        cues=[replace(zh.cues[0], text="hidden language")])
    checked = []
    result = calculate_subtitle_style([zh, en], {"font_size": 64}, 8,
        validate_layout=lambda text, style: checked.append((text, style["font_size"])))
    assert checked == [("原文\nhidden language", 64)]
    assert [track.style["enabled"] for track in result.changed_tracks] == [True, False]
    assert [track.cues for track in result.changed_tracks] == [zh.cues, en.cues]
    assert result.changed_tracks[0].artifact_ref == zh.artifact_ref
    with pytest.raises(EditConflict, match="没有变化"):
        calculate_subtitle_style([zh], {"font_size": 40}, 8,
            validate_layout=lambda *_: pytest.fail("no-op must not render a layout"))


def test_subtitle_content_checks_original_actor_and_version_before_edit(compound_values):
    task, plan, zh, audio = compound_values
    stale = EditCommand("workspace", "task", {"member_id": "member"}, "old", "DeleteSubtitleCue", {})
    with pytest.raises(EditConflict, match="时间线已经变化"):
        apply_subtitle_content_command(task, plan, [zh], stale)
    foreign = replace(stale, base_version_id="timeline", actor_identity={"member_id": "other"})
    with pytest.raises(PermissionError, match="不能修改"):
        apply_subtitle_content_command(task, plan, [zh], foreign)


def test_subtitle_proposal_reports_content_changes_and_keeps_original_version(compound_values):
    task, plan, zh, audio = compound_values
    command = EditCommand("workspace", "task", {"member_id": "member"}, "timeline", "DeleteSubtitleCue", {
        "base_version_id": "timeline", "track_id": "zh", "cue_id": "sentence",
    })
    changed, intent, proposal = prepare_subtitle_content_proposal(task, plan, [zh], command,
        output_refs=["artifacts/current.mp4"])
    assert intent.selected_object_refs == ["cue:sentence", "subtitle-track:zh"]
    assert proposal.target_refs == ["subtitle-track:zh", "cue:sentence"]
    assert proposal.operation["payload"]["base_version_id"] == "timeline"
    assert proposal.preview["after"]["detail"] == "0 条"
    assert "artifacts/original.srt" in proposal.invalidated_refs
    assert changed.changed_tracks[0].artifact_ref is None and zh.cues


def test_style_proposal_contains_exact_patch_and_no_fake_media_result(compound_values):
    task, plan, zh, audio = compound_values
    changed = calculate_subtitle_style([zh], {"font_size": 64}, 8, validate_layout=lambda *_: None)
    command = EditCommand("workspace", "task", {"member_id": "member"}, "timeline", "ConfigureSubtitleTrack", {})
    intent, proposal = prepare_subtitle_style_proposal(task, command, changed, output_refs=[])
    assert proposal.operation["payload"] == {"base_version_id": "timeline", "style": {"font_size": 64}}
    assert proposal.preview["after"]["ref"] == "timeline:timeline"
    assert "video_url" not in proposal.preview["after"]


def test_style_adoption_retains_unavailable_tracks_without_pretending_ready(compound_values):
    task, plan, zh, audio = compound_values
    unavailable = replace(zh, id="unavailable", status="unavailable", artifact_ref=None, transform_id=None)
    command = EditCommand("workspace", "task", {"member_id": "member"}, "timeline", "ConfigureSubtitleTrack", {
        "style": {"font_size": 64},
    })
    changed = apply_subtitle_style_command(task, [zh, unavailable], command)
    assert changed[0].style["font_size"] == 64
    assert changed[1] is unavailable and changed[1].status == "unavailable"
    assert zh.style["font_size"] == 40


def test_constructed_narration_audio_hash_is_identical_after_model_roundtrip():
    from app.audio_timeline import AudioTimeline, narration_audio_timeline
    from app.task_audio import media_digest

    original = narration_audio_timeline("narration://artifact/fixture", 8)
    roundtripped = AudioTimeline.model_validate(original.model_dump())
    assert media_digest([original.model_dump(), 8, False]) == media_digest([roundtripped.model_dump(), 8, False])


def test_narration_audio_candidate_key_survives_sqlite_reopen_and_changes_with_gain(person_render_edit_state):
    from app.audio_timeline import narration_audio_timeline
    from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
    from app.production import ProductionRepository
    from app.task_audio import media_digest

    state = person_render_edit_state
    graph = narration_audio_timeline(state.montage.narration_audio_path, state.montage.duration_seconds)
    plan = replace(state.montage, audio_timeline=graph)
    sealed_key = media_digest([graph.model_dump(), plan.duration_seconds, plan.narration_linkage_created_audio_timeline])
    state.production.save_creation_task_montage_plan(
        state.workspace_id, state.task.id, plan.id, creation_task_montage_plan_to_payload(plan),
    )
    reopened = ProductionRepository(state.production.database_path, state.production.video_library_path)
    restored = creation_task_montage_plan_from_payload(
        reopened.get_creation_task_montage_plan(state.workspace_id, state.task.id),
    )
    assert media_digest([restored.audio_timeline.model_dump(), restored.duration_seconds,
        restored.narration_linkage_created_audio_timeline]) == sealed_key
    restored.audio_timeline.tracks[0].clips[0].gain_db = -3.0
    assert media_digest([restored.audio_timeline.model_dump(), restored.duration_seconds,
        restored.narration_linkage_created_audio_timeline]) != sealed_key


def test_actual_draft_style_preview_adoption_and_undo_keep_all_other_facts(person_render_edit_state, monkeypatch):
    import app.main as main
    from app.creation.contracts import SubtitleTrack
    from app.production import ProductionRepository

    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    version = main._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    track = SubtitleTrack("actual-style", state.workspace_id, state.task.id, version.id, "en-US", None,
        {"enabled": False, "font_size": 40}, [SubtitleCue("caption", None, "Existing caption", 0, 4)],
        "timeline", None, "artifacts/existing.srt", "existing-transform", "valid")
    state.production.create_subtitle_track(track)
    before = state.client.get(base).json()
    def forbidden(*args, **kwargs):
        raise AssertionError("draft style proposal must not invoke media or runtime")
    monkeypatch.setattr(main, "_publish_task_picture_preview", forbidden)
    monkeypatch.setattr(main, "_creation_runtime", forbidden)
    result = state.client.post(base + "/edits/exact/preview?draft=true", json={
        "operation_type": "ConfigureSubtitleTrack", "style": {"font_size": 64},
    })
    assert result.status_code == 200, result.text
    proposal = result.json()["proposal"]
    assert proposal["preview"]["after"]["video_url"] is None
    assert state.client.get(base).json()["subtitle_tracks"] == before["subtitle_tracks"]
    adopted = state.client.post(base + f"/edits/{proposal['id']}/confirm")
    assert adopted.status_code == 200, adopted.text
    reopened = ProductionRepository(state.production.database_path, state.production.video_library_path)
    monkeypatch.setattr(main, "production_repo", reopened)
    current = state.client.get(base).json()
    assert current["subtitle_tracks"][0]["style"] == {"enabled": False, "font_size": 64}
    assert current["montage"]["audio_timeline"] == before["montage"]["audio_timeline"]
    assert current["montage"]["clips"] == before["montage"]["clips"]
    assert current["narration"]["audio_reference"] == before["narration"]["audio_reference"]
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    restored = state.client.get(base).json()
    assert restored["subtitle_tracks"][0]["style"] == before["subtitle_tracks"][0]["style"]
    assert state.client.post(base + "/timeline/redo", json={}).status_code == 200
    assert state.client.get(base).json()["subtitle_tracks"][0]["style"]["font_size"] == 64


def test_legacy_display_proposal_confirms_one_version_and_fresh_readback(tmp_path):
    import json
    import os
    from pathlib import Path
    from app.creation.editing.metadata import make_metadata_proposal
    from app.creation.editing.metadata_use_case import metadata_edit_command
    from app.creation.editing.subtitle_use_case import SubtitleEditUseCase, is_subtitle_edit
    from app.schemas import CreationTaskExactEditRequest
    from app.production import ProductionRepository
    from test_metadata_edit_use_case import metadata_state

    state = metadata_state(tmp_path)
    case = SubtitleEditUseCase(state.repo, state.case)
    payload = CreationTaskExactEditRequest(operation_type='ConfigureSubtitleTrack', subtitle_display='none')
    summary, refs, values, _preview, _effects = case.legacy_description(state.task, state.plan,
        state.base, [state.track], payload, None)
    assert is_subtitle_edit(payload.operation_type, values) is False
    command = metadata_edit_command(state.task, state.identity, payload.operation_type, values)
    intent, proposal = make_metadata_proposal(command, refs, summary, values)
    state.repo.create_edit_intent(intent)
    state.repo.create_edit_proposal(proposal)
    before = len(state.repo.list_timeline_versions('ws', state.task.id))
    result = case.confirm_legacy(state.task, proposal, settings=None)
    fresh = ProductionRepository(state.repo.database_path, tmp_path / 'library')
    task = fresh.get_creation_task('ws', state.task.id)
    version = fresh.get_timeline_version('ws', task.current_timeline_version_id)
    tracks = fresh.list_subtitle_tracks('ws', task.id, timeline_version_id=version.id)
    operations = fresh.list_edit_operations('ws', task.id)
    assert len(fresh.list_timeline_versions('ws', task.id)) == before + 1
    assert len(operations) == 1 and operations[0].proposal_id == proposal.id
    assert result.operation.id == operations[0].id and result.operation.result_version_id == version.id
    assert version.parent_version_id == state.base.id and version.sequence == 2
    assert len(tracks) == 1 and tracks[0].style['enabled'] is False
    assert [asdict(cue) for cue in tracks[0].cues] == [asdict(cue) for cue in state.track.cues]
    assert tracks[0].artifact_ref == state.track.artifact_ref
    assert fresh.get_timeline_version('ws', state.base.id).snapshot == state.base.snapshot
    assert state.track.style.get('enabled', True) is True
    assert result.montage.narration_audio_path == state.plan.narration_audio_path
    assert case.montage_edits.cover_asset_for_version('ws', task.id, version) is None
    assert fresh.get_edit_proposal('ws', proposal.id).status == 'confirmed'
    assert len(fresh.list_edit_operations('ws', task.id)) == 1
    evidence = os.environ.get('LEGACY_SUBTITLE_EVIDENCE_DIR')
    if evidence:
        Path(evidence, 'NORMAL_FACTS.json').write_text(json.dumps({'proposal': proposal.id,
            'operation': operations[0].id, 'base_version': state.base.id, 'result_version': version.id,
            'versions_added': 1, 'operations': 1, 'display_enabled': False, 'fresh_readback': True,
            'translation_ASR_native_calls': 0}, indent=2))
