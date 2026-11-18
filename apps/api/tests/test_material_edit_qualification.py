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
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
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
    proposals = state.production.edit_records.list_edit_proposals(state.workspace_id, state.task.id)
    candidates = state.client.get(f"{state.base_url}/montage/clips/{state.clip.id}/candidates")
    assert candidates.status_code == 200
    assert state.asset.id not in {item["asset_id"] for item in candidates.json()}
    response = _preview(state, operation)
    assert response.status_code == 403, response.text
    assert _current(state) == before
    assert state.production.edit_records.list_edit_proposals(state.workspace_id, state.task.id) == proposals


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
    assert state.production.edit_records.get_edit_proposal(state.workspace_id, proposal_id).status == "proposed"


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


@pytest.mark.parametrize("condition", ["rejected", "restricted", "reference", "reference_use", "missing", "changed", "range", "analysis"])
def test_current_material_qualification_blocks_invalid_source(material_edit_state, condition):
    state = material_edit_state
    if condition == "rejected":
        state.asset.review_status = "rejected"
    elif condition == "restricted":
        state.asset.use_state = "excluded"
    elif condition == "reference":
        state.asset.segments[0].usage_role = "exclude"
    elif condition == "reference_use":
        state.asset.use_state = "reference_only"
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


@pytest.mark.parametrize("crowded_ranking", ["manual_search", "montage_candidates"])
def test_described_replacement_ranks_eligible_candidates_before_limit(material_edit_state, tmp_path, crowded_ranking):
    state = material_edit_state
    workspace = state.workspace_id
    query = "特制蓝色齿轮"
    state.asset.segments[0].description = query
    state.production._save_asset(state.asset)

    # One additional file supplies more than a page of overlapping choices.
    path = tmp_path / "other-candidates.mp4"
    path.write_bytes(b"metadata-only fixture")
    asset_id = "other-candidates"
    manual_only = crowded_ranking == "manual_search"
    description = query if manual_only else state.montage.cues[0].text
    state.production._save_asset(replace(
        state.asset, id=asset_id, file_name=path.name, path=str(path),
        size_bytes=path.stat().st_size,
        modified_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
        use_state="not_for_automation" if manual_only else "available",
        segments=[replace(
            state.asset.segments[0], id=f"other-{index}", asset_id=asset_id,
            description=description, user_tags=[query] if manual_only else [],
        ) for index in range(25)],
    ))

    candidates_url = f"{state.base_url}/montage/clips/{state.clip.id}/candidates"
    manual_list = state.client.get(candidates_url)
    assert manual_list.status_code == 200, manual_list.text
    assert len(manual_list.json()) == 24
    automatic_list = state.client.get(f"{candidates_url}?for_automation=true")
    assert automatic_list.status_code == 200, automatic_list.text
    automatic_ids = [item["asset_id"] for item in automatic_list.json()]
    assert (state.clip.asset_id, state.clip.segment_id) not in {
        (item["asset_id"], item["segment_id"]) for item in automatic_list.json()
    }
    if manual_only:
        ordinary = state.production.material_search.search_material_segments(
            workspace, query=query, usage_roles=["a_roll", "b_roll"], min_duration_seconds=4,
            limit=24, viewer_user_id=state.member.member_id,
        )
        assert {item.asset_ref for item in ordinary} == {f"asset:{asset_id}"}
        assert state.asset.id in automatic_ids
        assert asset_id not in automatic_ids
    else:
        assert state.asset.id not in {item["asset_id"] for item in manual_list.json()}
        assert automatic_ids == [asset_id] * 24

    before = _current(state)
    response = state.client.post(f"{state.base_url}/edits/preview", json={
        "raw_command": f"把这个镜头换成{query}", "context_stage": "montage",
        "selected_object_refs": [f"clip:{state.clip.id}"],
    })
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    assert proposal["operation"]["payload"]["asset_id"] == state.asset.id
    assert _current(state) == before
    confirmed = state.client.post(f"{state.base_url}/edits/{proposal['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert (state.clip.id, state.asset.id, state.asset.segments[0].id) in _current(state)[1]


def test_described_replacement_uses_source_duration_at_current_speed(material_edit_state):
    state = material_edit_state
    query = "特制蓝色齿轮"
    segment = state.asset.segments[0]
    state.asset.segments = [replace(segment, start_seconds=1, end_seconds=3, description=query)]
    state.production._save_asset(state.asset)
    speed_edit = state.client.post(f"{state.base_url}/edits/exact", json={
        "operation_type": "ChangeClipSpeed", "clip_id": state.clip.id, "playback_rate": 0.5,
    })
    assert speed_edit.status_code == 200, speed_edit.text

    request = {"context_stage": "montage", "selected_object_refs": [f"clip:{state.clip.id}"]}
    before = _current(state)
    unmatched = state.client.post(f"{state.base_url}/edits/preview", json={
        **request, "raw_command": "把这个镜头换成北极企鹅",
    })
    assert unmatched.status_code == 409, unmatched.text
    assert unmatched.json()["detail"]["error"] == "replacement_clip_unavailable"
    assert _current(state) == before

    response = state.client.post(f"{state.base_url}/edits/preview", json={
        **request, "raw_command": f"把这个镜头换成{query}",
    })
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    assert "1.0–3.0 秒" in proposal["preview"]["after"]["detail"]
    confirmed = state.client.post(f"{state.base_url}/edits/{proposal['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    montage = creation_task_montage_plan_from_payload(
        state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)
    )
    clip = next(item for item in montage.clips if item.id == state.clip.id)
    assert (clip.asset_id, clip.segment_id) == (state.asset.id, segment.id)
    assert (clip.source_start_seconds, clip.source_end_seconds) == (1, 3)
    assert (clip.timeline_start_seconds, clip.timeline_end_seconds) == (
        state.clip.timeline_start_seconds, state.clip.timeline_end_seconds,
    )
