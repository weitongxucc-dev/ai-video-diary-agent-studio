from dataclasses import replace
from datetime import datetime, timezone

import pytest

import app.main as main_module
from app.montage import creation_task_montage_plan_from_payload
from test_montage_pipeline import person_render_edit_state, write_synthetic_video  # noqa: F401


@pytest.fixture
def material_edit_state(person_render_edit_state, tmp_path):  # noqa: F811 (explicit existing pytest fixture import)
    state = person_render_edit_state
    state.clip = state.montage.clips[1]
    main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    original = state.production.get_asset(state.workspace_id, state.clip.asset_id)
    path = tmp_path / "qualification-candidate.mp4"
    write_synthetic_video(path)
    state.asset = replace(
        original, id="qualification-candidate", file_name=path.name, path=str(path),
        size_bytes=path.stat().st_size,
        modified_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
        segments=[replace(segment, id=f"candidate-{segment.id}", asset_id="qualification-candidate")
                  for segment in original.segments],
    )
    state.production._save_asset(state.asset)
    with state.production._connect() as connection:
        state.production._claim_workspace_media_reference(
            connection, workspace_id=state.workspace_id,
            reference=state.production.workspace_media_reference_for_asset(state.workspace_id, state.asset.id),
        )
    state.base_url = f"/api/creation-tasks/{state.task.id}"
    return state


def _preview(state, operation):
    return state.client.post(f"{state.base_url}/edits/exact/preview", json={
        "operation_type": operation, "clip_id": state.clip.id,
        "asset_id": state.asset.id, "segment_id": state.asset.segments[0].id,
        "source_start_seconds": 0,
    })


def _current(state):
    task = state.production.get_creation_task(state.workspace_id, state.task.id)
    montage = creation_task_montage_plan_from_payload(
        state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)
    )
    return task.current_timeline_version_id, [(clip.id, clip.asset_id, clip.segment_id) for clip in montage.clips]


@pytest.mark.parametrize("operation", ["ReplaceClip", "InsertClip"])
def test_private_other_member_known_id_is_rejected_before_proposal(material_edit_state, operation):
    state = material_edit_state
    member, _token = state.members.join(state.members.create_invite_code(), "合成另一成员")
    state.asset.owner_user_id = member.member_id
    state.production._save_asset(state.asset)
    state.production.set_asset_visibility(state.workspace_id, state.asset.id, "private")
    before = _current(state)
    proposals = state.production.list_edit_proposals(state.workspace_id, state.task.id)
    candidates = state.client.get(f"{state.base_url}/montage/clips/{state.clip.id}/candidates")
    assert candidates.status_code == 200
    assert state.asset.id not in {item["asset_id"] for item in candidates.json()}
    response = _preview(state, operation)
    assert response.status_code == 403, response.text
    assert _current(state) == before
    assert state.production.list_edit_proposals(state.workspace_id, state.task.id) == proposals


@pytest.mark.parametrize("operation", ["ReplaceClip", "InsertClip"])
def test_private_permission_changed_after_preview_cannot_be_confirmed(material_edit_state, operation):
    state = material_edit_state
    response = _preview(state, operation)
    assert response.status_code == 200, response.text
    proposal_id = response.json()["proposal"]["id"]
    member, _token = state.members.join(state.members.create_invite_code(), "合成另一成员")
    state.asset.owner_user_id = member.member_id
    state.production._save_asset(state.asset)
    state.production.set_asset_visibility(state.workspace_id, state.asset.id, "private")
    before = _current(state)
    response = state.client.post(f"{state.base_url}/edits/{proposal_id}/confirm", json={})
    assert response.status_code == 403, response.text
    assert _current(state) == before
    assert state.production.get_edit_proposal(state.workspace_id, proposal_id).status == "proposed"


@pytest.mark.parametrize("operation", ["ReplaceClip", "InsertClip"])
def test_raw_valid_owned_material_can_be_chosen_and_committed(material_edit_state, operation):
    state = material_edit_state
    state.asset.review_status = "raw"
    for segment in state.asset.segments:
        segment.review_status = "raw"
    state.production._save_asset(state.asset)
    response = _preview(state, operation)
    assert response.status_code == 200, response.text
    confirmed = state.client.post(f"{state.base_url}/edits/{response.json()['proposal']['id']}/confirm", json={})
    assert confirmed.status_code == 200, confirmed.text
    assert any(clip[1] == state.asset.id for clip in _current(state)[1])


@pytest.mark.parametrize("condition", ["rejected", "restricted", "reference", "missing", "changed", "range", "analysis"])
def test_current_material_qualification_blocks_invalid_source(material_edit_state, condition):
    state = material_edit_state
    if condition == "rejected":
        state.asset.review_status = "rejected"
    elif condition == "restricted":
        state.asset.use_state = "unavailable"
    elif condition == "reference":
        state.asset.segments[0].usage_role = "reference_only"
    elif condition == "missing":
        from pathlib import Path
        Path(state.asset.path).unlink()
    elif condition == "changed":
        from pathlib import Path
        with Path(state.asset.path).open("ab") as stream:
            stream.write(b"changed")
    elif condition == "range":
        state.asset.segments[0].end_seconds = state.asset.duration_seconds + 1
    else:
        state.asset.analysis_status = "failed"
    state.production._save_asset(state.asset)
    before = _current(state)
    response = _preview(state, "ReplaceClip")
    assert response.status_code == 409, response.text
    assert _current(state) == before


def test_manual_choice_keeps_not_for_automation_but_automatic_list_excludes(material_edit_state):
    state = material_edit_state
    state.asset.use_state = "not_for_automation"
    state.production._save_asset(state.asset)
    viewer = state.asset.owner_user_id
    manual = main_module._creation_task_montage_candidates(state.workspace_id, state.montage, state.clip, viewer_user_id=viewer)
    automatic = main_module._creation_task_montage_candidates(state.workspace_id, state.montage, state.clip, automation_only=True, viewer_user_id=viewer)
    assert state.asset.id in {item.asset_id for item in manual}
    assert state.asset.id not in {item.asset_id for item in automatic}
