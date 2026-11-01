"""Shared fine-edit HTTP/version contracts; no live model or media executor."""
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

import app.main as main
from app.audio_timeline import TimelineAudioTrack
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.schemas import CreationTaskExactEditRequest
from app.visual_effects import VisualEffects
from _media_preview_test_fixtures import configure_synthetic_media_budgets
from test_material_edit_qualification import material_edit_state as material_edit_state  # noqa: F401
from test_montage_pipeline import person_render_edit_state as person_render_edit_state  # noqa: F401
from test_montage_pipeline import _granted_domain_preview_adapter


@pytest.fixture
def fine_state(material_edit_state, monkeypatch):
    state = material_edit_state
    monkeypatch.setattr(main.settings, "video_inbox_path", str(state.production.video_library_path), raising=False)
    monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", state.workspace_id)
    plan = creation_task_montage_plan_from_payload(state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id))
    graph = main._effective_audio_timeline(plan)
    voice = graph.tracks[0].clips[0]
    duration = main._task_audio().source_metadata(state.workspace_id, state.task.id, voice.source_ref)["duration_seconds"]
    voice.source_end_ms = min(voice.source_end_ms, round(duration * 1000))
    music = voice.model_copy(update={"id": "fine-music-clip", "name": "合成音乐", "gain_db": -2,
        "fade_in_ms": 100, "loudness_target_lufs": -20})
    graph.tracks.append(TimelineAudioTrack(id="fine-music", name="音乐", role="music", gain_db=-3, clips=[music]))
    plan.audio_timeline = graph
    plan.clips[0].visual_effects = VisualEffects(zoom=1.2, exposure=.4, fade_out_seconds=.2).model_dump()
    plan.clips[1].visual_effects = VisualEffects(zoom=1.4, exposure=.6, fade_out_seconds=.3).model_dump()
    state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id, state.task.id, plan.id,
        creation_task_montage_plan_to_payload(plan))
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    original = state.production.timeline_records.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    state.production.timeline_records.create_timeline_version(replace(original, id="fine-original-version", parent_version_id=original.id,
        sequence=original.sequence + 1, snapshot={**original.snapshot, "montage": creation_task_montage_plan_to_payload(plan)}))
    state.before = deepcopy(state.client.get(state.base_url).json())
    state.plan = plan
    return state


def bound(state, **action):
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    version = state.production.timeline_records.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    return {"request_id": str(uuid4()), "expected_timeline_version_id": version.id,
        "expected_timeline_sequence": version.sequence, **action}


def audio_action(*, action="update", target="fine-music-clip", values=None):
    return {"operation_type": "EditAudioTimeline", "audio_edit": {"action": action, "target_id": target,
        "values": values if values is not None else {"gain_db": -8, "fade_out_ms": 300}}}


def preview_and_apply(state, payload):
    url = state.base_url + "/edits/exact/preview?draft=true"
    preview = state.client.post(url, json=payload)
    assert preview.status_code == 200, preview.text
    assert state.client.post(url, json=payload).json() == preview.json()
    assert state.client.get(state.base_url).json()["task"]["current_timeline_version_id"] == payload["expected_timeline_version_id"]
    proposal = preview.json()["proposal"]
    adopted = state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm", json={"request_id": str(uuid4())})
    assert adopted.status_code == 200, adopted.text
    return proposal, state.client.get(state.base_url).json()


def test_catalog_exposes_shared_schema_units_and_true_nested_targets(fine_state):
    state = fine_state
    cap = state.client.get(state.base_url + "/assistant/edit-capabilities").json()
    assert cap["schema"] == CreationTaskExactEditRequest.model_json_schema()
    actions = {item["operation_type"]: item for item in cap["operations"]}
    assert actions["EditAudioTimeline"]["required_fields"] == ["audio_edit"]
    assert actions["AdjustVisualEffects"]["required_fields"] == ["clip_ids", "effects"]
    assert actions["EditAudioTimeline"]["available"] and actions["AdjustVisualEffects"]["available"]
    assert "毫秒" in cap["units"]["audio_edit.values.fade_out_ms"]
    assert "仅music" in cap["units"]["audio_edit.values.duck_under_voice"]
    assert "保持" in cap["units"]["patch"]
    assert "受许可" in cap["units"]["effects.beauty_strength"]
    limits = cap["schema"]["$defs"]["AudioMixPatch"]["properties"]
    assert limits["gain_db"]["minimum"] == -60 and limits["gain_db"]["maximum"] == 12
    assert "gain_points" not in limits and "playback_rate" not in limits


@pytest.mark.parametrize("action,target,values", [("update", "fine-music-clip", {"gain_db": -8, "fade_out_ms": 300}),
    ("track", "fine-music", {"gain_db": -9, "duck_under_voice": True}),
    ("update", "fine-music-clip", {"loudness_target_lufs": None, "denoise_db": 3, "muted": True})])
def test_audio_mix_patch_adoption_preserves_other_parameters_and_undo(fine_state, action, target, values):
    state = fine_state
    proposal, after = preview_and_apply(state, bound(state, **audio_action(action=action, target=target, values=values)))
    assert proposal["target_refs"] == [f"audio:{target}"]
    assert proposal["operation"]["payload"]["edit"]["values"] == values
    before_tracks = state.before["montage"]["audio_timeline"]["tracks"]
    tracks = after["montage"]["audio_timeline"]["tracks"]
    assert tracks[0] == before_tracks[0]
    music = tracks[-1] if action == "track" else tracks[-1]["clips"][0]
    previous = before_tracks[-1] if action == "track" else before_tracks[-1]["clips"][0]
    assert music == {**previous, **values}
    assert after["montage"]["clips"] == state.before["montage"]["clips"]
    assert after["montage"]["cues"] == state.before["montage"]["cues"]
    assert after["task"]["timeline_sequence"] == state.before["task"]["timeline_sequence"] + 1
    assert state.client.post(state.base_url + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(state.base_url).json()["montage"]["audio_timeline"] == state.before["montage"]["audio_timeline"]


def test_visual_patch_applies_per_clip_without_resetting_other_effects_or_audio(fine_state):
    state = fine_state
    targets = [clip.id for clip in state.plan.clips]
    proposal, after = preview_and_apply(state, bound(state, operation_type="AdjustVisualEffects", clip_ids=targets,
        effects={"saturation": .7}))
    assert proposal["target_refs"] == [f"clip:{ref}" for ref in targets]
    assert proposal["operation"]["payload"]["effects"] == {"saturation": .7}
    for previous, current in zip(state.before["montage"]["clips"], after["montage"]["clips"]):
        assert current == {**previous, "visual_effects": {**previous["visual_effects"], "saturation": .7}}
    assert after["montage"]["audio_timeline"] == state.before["montage"]["audio_timeline"]
    assert state.client.post(state.base_url + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(state.base_url).json()["montage"]["clips"] == state.before["montage"]["clips"]


@pytest.mark.parametrize("failure", ["missing", "empty", "wrong_control", "foreign", "old_version", "locked", "range", "unknown"])
def test_fine_edits_reject_invalid_request_without_version_or_media_effect(fine_state, failure):
    state = fine_state
    payload = bound(state, **audio_action())
    if failure == "missing":
        del payload["audio_edit"]
    elif failure == "empty":
        payload["audio_edit"]["values"] = {}
    elif failure == "wrong_control":
        payload["audio_edit"]["values"] = {"duck_under_voice": True}
    elif failure == "foreign":
        payload["audio_edit"]["target_id"] = "foreign-track"
    elif failure == "old_version":
        payload["expected_timeline_version_id"] = "old-version"
    elif failure == "range":
        payload["audio_edit"]["values"] = {"gain_db": -61}
    elif failure == "unknown":
        payload["audio_edit"]["values"] = {"playback_rate": 2}
    else:
        state.plan.audio_timeline.tracks[-1].locked = True
        state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id, state.task.id, state.plan.id,
            creation_task_montage_plan_to_payload(state.plan))
    response = state.client.post(state.base_url + "/edits/exact/preview?draft=true", json=payload)
    assert response.status_code in {409, 422}, response.text
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    assert task.current_timeline_version_id == payload["expected_timeline_version_id"] or failure == "old_version"
    assert state.production.edit_records.list_edit_operations(state.workspace_id, task.id) == []
    assert state.production.job_records.list_job_runs(state.workspace_id, task.id) == []


def test_visual_lock_and_full_default_omission_boundaries(fine_state):
    state = fine_state
    selected = state.plan.clips[1]
    selected.locked = True
    state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id, state.task.id, state.plan.id,
        creation_task_montage_plan_to_payload(state.plan))
    payload = bound(state, operation_type="AdjustVisualEffects", clip_ids=[selected.id], effects={"exposure": .8})
    response = state.client.post(state.base_url + "/edits/exact/preview?draft=true", json=payload)
    assert response.status_code == 409, response.text
    assert CreationTaskExactEditRequest.model_validate(payload).model_dump(exclude_unset=True)["effects"] == {"exposure": .8}
    assert state.production.edit_records.list_edit_operations(state.workspace_id, state.task.id) == []


@pytest.mark.parametrize("audio", [True, False])
def test_queued_fine_edit_keeps_sparse_dto_and_real_target_refs(fine_state, monkeypatch, audio):
    state = fine_state
    post = state.client.post
    runtime = _granted_domain_preview_adapter(state, monkeypatch)
    action = audio_action() if audio else {"operation_type": "AdjustVisualEffects", "clip_ids": [state.clip.id], "effects": {"exposure": .8}}
    try:
        response = post(state.base_url + "/edits/exact/preview", json=bound(state, **action))
        assert response.status_code == 202, response.text
        receipt = response.json()
        assert receipt["target_refs"] == ["audio:fine-music-clip" if audio else f"clip:{state.clip.id}"]
        saved = state.production.execution_records.get_task_event(state.workspace_id, f"event_{receipt['job_id']}_start").payload
        assert saved["arguments"]["payload"] == action
        assert saved["arguments"]["_typed_source"]["action"] == action
        assert len(state.production.job_records.list_job_runs(state.workspace_id, state.task.id)) == 1
    finally:
        runtime.scheduler.close()


def test_exact_mix_resources_use_existing_audio_class_and_fixed_sources(fine_state, monkeypatch):
    state = fine_state
    configure_synthetic_media_budgets(monkeypatch, main)
    payload, source = main._prepare_exact_edit_request(state.task.id, state.request,
        CreationTaskExactEditRequest.model_validate(bound(state, **audio_action())))
    actor = main._require_permit_actor(state.request, state.workspace_id)
    facts, resources = main._task_media_facts(state.workspace_id, state.task.id, "exact_edit_preview",
        {"payload": payload.model_dump(mode="json", exclude_unset=True), "_typed_source": source, "_media_kind": "audio_probe"}, actor)
    assert resources["kind"] == "task_audio" and resources["execution_mode"] == "cpu_ffmpeg"
    assert resources["input_count"] == 2 and resources["inputs"][0]["uses"] == 2
    assert resources["output_duration_limit_seconds"] == state.plan.duration_seconds
    assert facts["base_version_id"] == source["expected_timeline_version_id"] and facts["files"]


def test_color_patch_preserves_existing_cut_effect_clock_until_fade_is_changed(fine_state):
    clip = replace(fine_state.plan.clips[1], fade_envelope_duration_frames=240,
        fade_envelope_offset_frames=6, fade_envelope_rate=2)
    changed = main.with_visual_effects(clip, {"saturation": .5})
    assert (changed.fade_envelope_duration_frames, changed.fade_envelope_offset_frames, changed.fade_envelope_rate) == (240, 6, 2)
    assert changed.visual_effects["fade_out_seconds"] == clip.visual_effects["fade_out_seconds"]
    fade = main.with_visual_effects(clip, {"fade_out_seconds": .1})
    assert (fade.fade_envelope_duration_frames, fade.fade_envelope_offset_frames, fade.fade_envelope_rate) == (None, 0, 1)


def test_nonzero_face_effect_is_explicitly_unavailable_before_media_submission(fine_state):
    state = fine_state
    response = state.client.post(state.base_url + "/edits/exact/preview", json=bound(state,
        operation_type="AdjustVisualEffects", clip_ids=[state.clip.id], effects={"beauty_strength": .2}))
    assert response.status_code == 422 and "受许可执行路径" in response.text
    assert state.production.job_records.list_job_runs(state.workspace_id, state.task.id) == []
