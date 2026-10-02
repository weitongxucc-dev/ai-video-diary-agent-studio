"""Crop previews describe only positions that affect the chosen display mode."""
import pytest

import app.main as main_module
from test_montage_pipeline import person_render_edit_state


@pytest.mark.parametrize("mode,detail", [
    ("fit", "完整显示 · 裁剪位置已保留，在此模式下不生效"),
    ("fill", "填满画面 · 水平 62% · 垂直 41%"),
])
def test_crop_preview_description_matches_display_mode(person_render_edit_state, mode, detail):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    before = state.client.get(base).json()
    clip = before["montage"]["clips"][1]
    response = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "ChangeCrop", "clip_id": clip["id"],
        "fit_mode": mode, "crop_x": .62, "crop_y": .41,
    })
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    assert proposal["preview"]["after"]["detail"] == detail
    assert {key: proposal["operation"]["payload"][key] for key in ("fit_mode", "crop_x", "crop_y")} == {
        "fit_mode": mode, "crop_x": .62, "crop_y": .41,
    }
    assert state.client.get(base).json() == before
