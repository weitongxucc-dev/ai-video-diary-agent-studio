"""Crop route/source/version tests mock encoding, not playable media."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

import app.main as main_module
from app.production import TimelineVersion
from test_montage_pipeline import person_render_edit_state as person_render_edit_state


@pytest.fixture
def legal_crop_state(person_render_edit_state):
    state = person_render_edit_state
    # Both fixture clips must lie inside their approved material segment.
    clip = state.montage.clips[1]
    asset = state.production.get_asset(state.workspace_id, clip.asset_id)
    segment = next(segment for segment in asset.segments if segment.id == clip.segment_id)
    segment.start_seconds = clip.source_start_seconds
    state.production._save_asset(asset)
    return state


@pytest.fixture
def crop_previews(monkeypatch):
    calls = []

    def publish(repository, store, temporary_root, workspace_id, task_id, clip, source, portrait, **kwargs):
        calls.append({"workspace_id": workspace_id, "task_id": task_id,
                      "clip": asdict(clip), "source": source, **kwargs})
        identity = f"crop-preview-{len(calls)}"
        return SimpleNamespace(id=identity, logical_ref=f"artifacts/{identity}.mp4")

    monkeypatch.setattr(main_module, "publish_color_preview", publish)
    return calls


def preview_crop(state, clip_index=1, **values):
    base = f"/api/creation-tasks/{state.task.id}"
    main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    before = state.client.get(base).json()
    clip = before["montage"]["clips"][clip_index]
    response = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "ChangeCrop", "clip_id": clip["id"],
        "fit_mode": "fit", "crop_x": .62, "crop_y": .41, **values,
    })
    return base, before, clip, response


def advance_version(state, version_id):
    base = state.production.get_timeline_version(state.workspace_id, version_id)
    return state.production.create_timeline_version(TimelineVersion(
        id="timeline-crop-concurrent", workspace_id=state.workspace_id,
        task_id=state.task.id, parent_version_id=base.id, sequence=base.sequence + 1,
        snapshot=base.snapshot, created_by_operation_id=None,
    ))


@pytest.mark.parametrize("clip_index", [0, 1], ids=["person", "material"])
@pytest.mark.parametrize("mode,detail", [
    ("fit", "完整显示 · 裁剪位置已保留，在此模式下不生效"),
    ("fill", "填满画面 · 水平 62% · 垂直 41%"),
])
def test_crop_preview_description_matches_display_mode(legal_crop_state, crop_previews, clip_index, mode, detail):
    state = legal_crop_state
    base, before, clip, response = preview_crop(state, clip_index, fit_mode=mode)
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    assert proposal["preview"]["after"]["detail"] == detail
    assert {key: proposal["operation"]["payload"][key] for key in ("fit_mode", "crop_x", "crop_y")} == {
        "fit_mode": mode, "crop_x": .62, "crop_y": .41,
    }
    assert proposal["operation"]["payload"]["base_version_id"] == before["task"]["current_timeline_version_id"]
    assert len(crop_previews) == 2
    expected_source = (main_module._person_render_media_path(state.old_render) if clip_index == 0 else
                       state.production.resolve_clip_media_reference(state.workspace_id, clip["asset_id"], state.montage.clips[clip_index].source_path))
    for index, (side, call) in enumerate(zip(("before", "after"), crop_previews), 1):
        assert call["workspace_id"] == state.workspace_id and call["task_id"] == state.task.id
        assert call["source"] == expected_source
        assert call["kind"] == "color_preview" and call["aspect_ratio"] == before["montage"]["aspect_ratio"]
        target = dict(asdict(state.montage.clips[clip_index]))
        if side == "after":
            target.update(fit_mode=mode, crop_x=.62, crop_y=.41)
        assert call["clip"] == target
        assert proposal["preview"][side]["ref"] == f"artifacts/crop-preview-{index}.mp4"
        assert proposal["preview"][side]["video_url"] == base + f"/visual/artifacts/crop-preview-{index}"
    assert state.client.get(base).json() == before
    confirmed = state.client.post(base + f"/edits/{proposal['id']}/confirm", json={})
    assert confirmed.status_code == 200, confirmed.text
    after = state.client.get(base).json()
    operation = confirmed.json()["operation"]
    assert operation["base_version_id"] == before["task"]["current_timeline_version_id"]
    assert operation["result_version_id"] == after["task"]["current_timeline_version_id"]
    assert after["montage"]["clips"][clip_index]["fit_mode"] == mode
    assert after["montage"]["clips"][clip_index]["crop_x"] == .62
    assert after["montage"]["clips"][clip_index]["crop_y"] == .41
    for key in ("source_start_seconds", "source_end_seconds", "timeline_start_seconds", "timeline_end_seconds", "asset_id", "person_render_id"):
        assert after["montage"]["clips"][clip_index][key] == clip[key]
    assert after["montage"]["audio_timeline"] == before["montage"]["audio_timeline"]


@pytest.mark.parametrize("clip_index", [0, 1], ids=["person", "material"])
def test_crop_preview_rejects_unavailable_source(person_render_edit_state, crop_previews, monkeypatch, clip_index):
    state = person_render_edit_state
    if clip_index == 0:
        monkeypatch.setattr(main_module, "_person_render_media_path", lambda _render: None)
    else:
        def disconnected(*args):
            raise ValueError("源素材已断开")
        monkeypatch.setattr(state.production, "resolve_clip_media_reference", disconnected)
    base, before, _, response = preview_crop(state, clip_index)
    assert response.status_code == 422
    expected_message = "人物口型片段不可用" if clip_index == 0 else "源素材已断开"
    assert expected_message in response.text
    assert not crop_previews
    assert not state.production.list_edit_proposals(state.workspace_id, state.task.id)
    assert state.client.get(base).json() == before


def test_crop_preview_encoding_failure_is_not_a_proposal(person_render_edit_state, monkeypatch):
    state = person_render_edit_state

    def failed(*args, **kwargs):
        raise OSError("encoder failed")

    monkeypatch.setattr(main_module, "publish_color_preview", failed)
    base, before, _, response = preview_crop(state)
    assert response.status_code == 422
    assert response.json()["detail"] == "画面预览失败，请检查源素材、FFmpeg和外接盘"
    assert not state.production.list_edit_proposals(state.workspace_id, state.task.id)
    assert state.client.get(base).json() == before


def test_crop_preview_rechecks_version_after_publication(person_render_edit_state, monkeypatch):
    state = person_render_edit_state
    calls = []

    def concurrent(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            task = state.production.get_creation_task(state.workspace_id, state.task.id)
            advance_version(state, task.current_timeline_version_id)
        return SimpleNamespace(id=f"stale-{len(calls)}", logical_ref="artifacts/stale.mp4")

    monkeypatch.setattr(main_module, "publish_color_preview", concurrent)
    _, before, _, response = preview_crop(state)
    assert response.status_code == 409
    assert response.json()["detail"] == "预览期间时间线已变化，请重新预览"
    assert len(calls) == 2
    assert not state.production.list_edit_proposals(state.workspace_id, state.task.id)
    assert state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id != before["task"]["current_timeline_version_id"]


def test_crop_preview_cannot_confirm_after_version_changes(person_render_edit_state, crop_previews):
    state = person_render_edit_state
    base, before, _, response = preview_crop(state)
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    newer = advance_version(state, before["task"]["current_timeline_version_id"])
    response = state.client.post(base + f"/edits/{proposal['id']}/confirm", json={})
    assert response.status_code == 409
    assert state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == newer.id
    assert len(crop_previews) == 2
