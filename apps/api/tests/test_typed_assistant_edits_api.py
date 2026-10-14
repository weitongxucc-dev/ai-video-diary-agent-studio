"""Typed edits through isolated domain/API state; no live model or service."""
import json
from dataclasses import replace
from uuid import uuid4

import pytest

import app.main as main
from app.conversation_repository import ConversationRepository
from app.schemas import CreationTaskExactEditRequest
from test_material_edit_qualification import material_edit_state as material_edit_state  # noqa: F401
from test_montage_pipeline import person_render_edit_state as person_render_edit_state, _granted_domain_preview_adapter  # noqa: F401
from test_linked_timeline_reorder import _build_linked_state


@pytest.fixture(autouse=True)
def typed_settings(request, monkeypatch):
    if "material_edit_state" in request.fixturenames:
        material_edit_state = request.getfixturevalue("material_edit_state")
        monkeypatch.setattr(main, "settings", replace(main.settings,
            video_inbox_path=str(material_edit_state.production.video_library_path)))


def bound(state, **action):
    task = state.production.get_creation_task(state.workspace_id, state.task.id)
    version = state.production.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    return {"request_id": str(uuid4()), "expected_timeline_version_id": version.id,
            "expected_timeline_sequence": version.sequence, **action}


def replace_action(state):
    return {"operation_type": "ReplaceClip", "clip_id": state.clip.id,
            "asset_id": state.asset.id, "segment_id": state.asset.segments[0].id}


@pytest.fixture
def metadata_http_state(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_metadata_edit_use_case import metadata_state
    state = metadata_state(tmp_path)
    monkeypatch.setattr(main, "production_repo", state.repo)
    monkeypatch.setattr(main, "artifact_store", state.store)
    monkeypatch.setattr(main, "local_members_service", state.members)
    monkeypatch.setattr(main, "current_workspace_id", lambda request: request.headers.get("x-test-workspace", "ws"))
    monkeypatch.setattr(main, "settings", replace(main.settings,
        video_inbox_path=str(state.repo.video_library_path), video_output_path=str(tmp_path / "outputs")))
    state.client = TestClient(main.app, headers={"X-Local-Member-Token": state.token})
    state.url = f"/api/creation-tasks/{state.task.id}"
    return state


def metadata_payload(state):
    return {"operation_type": "ChangeCrop", "clip_id": "clip", "fit_mode": "fit", "crop_x": .3, "crop_y": .7,
        "request_id": str(uuid4()), "expected_timeline_version_id": state.base.id, "expected_timeline_sequence": 1}


def test_metadata_http_save_replay_input_conflict_and_lost_receipt(metadata_http_state, monkeypatch):
    state = metadata_http_state
    wakes = []
    monkeypatch.setattr(main, '_wake_assistant_continuations', lambda: wakes.append('notified'))
    payload = metadata_payload(state)
    response = state.client.post(state.url + "/edits/exact", json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert len(wakes) == 1
    assert state.client.post(state.url + "/edits/exact", json=payload).json() == saved
    assert len(wakes) == 1
    assert state.client.post(state.url + "/edits/exact", json={**payload, "crop_x": .9}).status_code == 409
    with state.repo._connect() as conn:
        conn.execute("DELETE FROM task_events WHERE event_type='edit_request_result'")
    recovered = state.client.post(state.url + "/edits/exact", json=payload)
    assert recovered.status_code == 200, recovered.text
    assert len(wakes) == 2
    assert recovered.json()["operation"] == saved["operation"]
    assert state.repo.get_creation_task('ws', state.task.id).narration_cache_key == state.narration.cache_key
    assert state.repo.get_timeline_version('ws', state.base.id).snapshot == state.base.snapshot
    assert len(state.repo.list_edit_operations('ws', state.task.id)) == 1


def test_metadata_patch_preserves_no_change_and_missing_target_responses(metadata_http_state):
    state = metadata_http_state
    unchanged = state.client.patch(state.url + '/montage/cues/cue', json={'text': '原字幕'})
    assert unchanged.status_code == 409, unchanged.text
    assert unchanged.json()['detail'] == {'error': 'edit_no_change', 'reason': '字幕文字、时轴和样式没有变化。',
        'impact': '没有创建新的时间线版本。', 'repair_action': '修改文字、断行、强调词或字幕样式后再预览。', 'retryable': False}
    missing = state.client.patch(state.url + '/montage/cues/missing', json={'text': '新字幕'})
    assert missing.status_code == 404, missing.text
    assert state.repo.get_creation_task('ws', state.task.id).current_timeline_version_id == state.base.id
    assert state.repo.list_edit_operations('ws', state.task.id) == []


def test_metadata_patch_routes_consume_use_case_without_route_reentry(metadata_http_state, monkeypatch):
    state = metadata_http_state
    def forbidden(*args, **kwargs):
        pytest.fail("metadata PATCH must not invoke another HTTP route")
    monkeypatch.setattr(main, "apply_creation_task_exact_edit", forbidden)
    clip = state.client.patch(state.url + "/montage/clips/clip", json={"locked": True})
    assert clip.status_code == 200, clip.text
    subtitle = state.client.patch(state.url + "/montage/cues/cue", json={"text": "只更新这句字幕"})
    assert subtitle.status_code == 200, subtitle.text
    current = state.repo.get_creation_task('ws', state.task.id)
    plan = state.repo.get_creation_task_montage_plan('ws', state.task.id)
    assert plan['clips'][0]['locked'] is True
    assert plan['cues'][0]['text'] == "只更新这句字幕"
    assert plan['narration_audio_path'] == state.plan.narration_audio_path
    assert current.narration_cache_key == state.narration.cache_key
    assert len(state.repo.list_timeline_versions('ws', state.task.id)) == 3


def test_metadata_http_refuses_stale_or_foreign_inputs(metadata_http_state):
    state = metadata_http_state
    payload = metadata_payload(state)
    assert state.client.post(state.url + "/edits/exact", json={**payload, "expected_timeline_sequence": 2}).status_code == 409
    assert state.client.post(state.url + "/edits/exact", headers={"x-test-workspace": "foreign"}, json=metadata_payload(state)).status_code == 404
    assert state.client.post("/api/creation-tasks/foreign/edits/exact", json=metadata_payload(state)).status_code == 404
    _, token = state.members.join(state.members.create_invite_code(), "other metadata member")
    denied = state.client.post(state.url + "/edits/exact", headers={"X-Local-Member-Token": token}, json=metadata_payload(state))
    assert denied.status_code in {403, 404}, denied.text
    assert state.repo.get_creation_task('ws', state.task.id).current_timeline_version_id == state.base.id
    assert state.repo.list_edit_operations('ws', state.task.id) == []


def test_capabilities_are_real_schema_and_exclude_unmigrated_actions(material_edit_state):
    state = material_edit_state
    response = state.client.get(state.base_url + "/assistant/edit-capabilities")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["schema"] == CreationTaskExactEditRequest.model_json_schema()
    assert {"ReplaceClip", "TrimClip", "ChangeClipSpeed", "ChangeCrop", "UpdateSubtitleCue"} <= set(value["supported_operations"])
    assert not {"RegenerateNarrationSegment", "ChangePersonSegment", "RegeneratePersonRender"} & set(value["supported_operations"])
    assert "保留" in value["units"]["playback_rate"]
    assert "source_span=timeline_span*rate" in value["units"]["playback_rate"]
    assert "2.5*0.7=1.75" in value["units"]["playback_rate"]
    assert "ChangeLinkedSpeed：相对当前速度倍率" in value["units"]["playback_rate"]
    assert "source_span=timeline_span*rate" in value["schema"]["properties"]["playback_rate"]["description"]
    assert state.client.get("/api/creation-tasks/missing/assistant/edit-capabilities").status_code == 404


def test_typed_replace_replay_lost_receipt_and_undo_keep_narration(material_edit_state):
    state = material_edit_state
    before = state.client.get(state.base_url).json()
    original_audio = state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)["narration_audio_path"]
    payload = bound(state, **replace_action(state))
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload).json() == preview.json()
    proposal = preview.json()["proposal"]
    result_url = state.base_url + f"/edits/{proposal['id']}/result"
    binding = state.client.get(result_url).json()
    apply = {"request_id": str(uuid4()), "expected_base_version_id": binding["base_version_id"],
             "expected_input_digest": binding["input_digest"]}
    url = state.base_url + f"/edits/{proposal['id']}/confirm"
    confirmed = state.client.post(url, json=apply)
    assert confirmed.status_code == 200, confirmed.text
    after = state.client.get(state.base_url).json()
    assert state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)["narration_audio_path"] == original_audio
    assert after["montage"]["cues"] == before["montage"]["cues"]
    assert after["task"]["timeline_sequence"] == before["task"]["timeline_sequence"] + 1
    assert state.client.post(url, json=apply).json() == confirmed.json()
    # Simulate receipt loss after the actual operation commit. Recover from
    # existing proposal/operation/version lineage, without a second apply.
    with state.production._connect() as conn:
        conn.execute("DELETE FROM task_events WHERE event_type='edit_request_result' AND json_extract(payload_json,'$.response.operation.proposal_id')=?", (proposal["id"],))
    recovered = state.client.post(url, json=apply)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["operation"] == confirmed.json()["operation"]
    assert state.client.get(result_url).json()["result"]["operation"] == confirmed.json()["operation"]
    assert len([op for op in state.production.list_edit_operations(state.workspace_id, state.task.id) if op.proposal_id == proposal["id"]]) == 1
    assert state.client.post(state.base_url + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(state.base_url).json()["montage"]["clips"] == before["montage"]["clips"]
    assert state.client.post(url, json=apply).json()["operation"] == confirmed.json()["operation"]
    changed = {**payload, "source_start_seconds": 5}
    assert state.client.post(state.base_url + "/edits/exact/preview", json=changed).status_code == 409


@pytest.mark.parametrize("action", [
    {"operation_type": "TrimClip", "source_start_seconds": 9},
    {"operation_type": "ChangeClipSpeed", "playback_rate": 0.7},
    {"operation_type": "ChangeCrop", "fit_mode": "fill", "crop_x": 0.2, "crop_y": 0.5},
])
def test_exact_typed_actions_use_existing_domain_preview(material_edit_state, action):
    state = material_edit_state
    request = bound(state, clip_id=state.clip.id, **action)
    response = state.client.post(state.base_url + "/edits/exact/preview?draft=true", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["operation"]["operation_type"] == action["operation_type"]
    intent = state.production.get_edit_intent(state.workspace_id, response.json()["intent"]["id"])
    assert intent.constraints["request_source"]["request_id"] == request["request_id"]


@pytest.mark.parametrize("mutation", ["old_version", "old_sequence", "other_clip", "unrelated_field", "extra", "nonfinite"])
def test_typed_requests_refuse_invalid_scope_and_fields(material_edit_state, mutation):
    state = material_edit_state
    payload = bound(state, **replace_action(state))
    if mutation == "old_version":
        payload["expected_timeline_version_id"] = "old"
    elif mutation == "old_sequence":
        payload["expected_timeline_sequence"] += 1
    elif mutation == "other_clip":
        payload["clip_id"] = "another-task-clip"
    elif mutation == "unrelated_field":
        payload["text"] = "must not be silently ignored"
    elif mutation == "extra":
        payload["invented"] = 1
    else:
        payload["source_start_seconds"] = float("inf")
    before = state.client.get(state.base_url).json()["task"]["current_timeline_version_id"]
    response = state.client.post(state.base_url + "/edits/exact/preview", content=json.dumps(payload), headers={"Content-Type": "application/json"})
    assert response.status_code in {404, 409, 422}, response.text
    assert state.client.get(state.base_url).json()["task"]["current_timeline_version_id"] == before


def test_assistant_typed_source_is_host_bound_and_not_parsed_again(material_edit_state, tmp_path, monkeypatch):
    state = material_edit_state
    conversations = ConversationRepository(tmp_path / "conversations.sqlite3")
    conversations.initialize()
    monkeypatch.setattr(main, "conversation_repo", conversations)
    member = state.members.resolve_member(state.client.headers.get("X-Local-Member-Token"))
    conversation = conversations.create(state.workspace_id, member.member_id, "task", state.task.id)
    payload = bound(state, **replace_action(state))
    raw = "请把第二段换成那个更合适的画面，旁白维持原样"
    conversation = conversations.save_draft(conversation["id"], state.workspace_id, member.member_id, raw, conversation["draft_version"])
    turn, _ = conversations.create_turn_idempotent(conversation["id"], state.workspace_id, member.member_id,
        raw, str(uuid4()), conversation["draft_version"], request_context={"kind": "task", "task_id": state.task.id,
        "target_snapshot": {"timeline_version_id": payload["expected_timeline_version_id"], "timeline_sequence": payload["expected_timeline_sequence"]}})
    payload["assistant_turn_id"] = turn["id"]
    response = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["intent"]["raw_command"] == raw
    # Submit through the real persisted JobRun/DeviceScheduler path using the
    # existing explicit media substitutes, then end Pi before executing it.
    original_post = state.client.post
    runtime = _granted_domain_preview_adapter(state, monkeypatch)
    from test_creation_resource_contract import _render_budget
    monkeypatch.setattr(main, 'clip_preview_budget_from_env', lambda:_render_budget())
    media = runtime.registrations['creation_job'].owner.task_media
    media.facts = main._task_media_facts
    def render_input(inputs, path, context):
        # Domain lineage seam only; playable media has separate R1 regressions.
        context.check()
        assert context.grant.workspace_id == state.workspace_id
        state.preview_inputs.append(inputs)
        path.write_bytes(b'domain-render-receipt')
    monkeypatch.setattr('app.media.clip_preview.render_clip_input', render_input)
    crop = bound(state, operation_type="ChangeCrop", clip_id=state.clip.id,
                 fit_mode="fill", crop_x=0.2, crop_y=0.5, assistant_turn_id=turn["id"])
    queued = original_post(state.base_url + "/edits/exact/preview", json=crop)
    assert queued.status_code == 202, queued.text
    event = state.production.get_task_event(state.workspace_id, f"event_{queued.json()['job_id']}_start")
    command = event.payload['arguments']['_clip_preview']
    assert command['base_version_id'] == crop['expected_timeline_version_id']
    assert command['values'] == {'fit_mode':'fill', 'crop_x':.2, 'crop_y':.5}
    assert command['source']['assistant_turn_id'] == turn['id']
    conversations.finish_turn(turn["id"], "completed", "已准备修改")
    assert runtime.run_once()
    settled = state.client.get(queued.json()["result_url"])
    assert settled.status_code == 200, settled.text
    assert settled.json()["status"] == "succeeded", settled.text
    assert settled.json()["result"]["intent"]["raw_command"] == raw
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload).json() == response.json()
    payload["request_id"] = str(uuid4())
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload).status_code == 409


@pytest.mark.parametrize("use_request_identity", [True, False])
def test_source_revision_and_apply_identity_cannot_change_after_preview(material_edit_state, use_request_identity):
    state = material_edit_state
    payload = bound(state, **replace_action(state))
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    before = state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id
    with open(state.asset.path, "ab") as source:
        source.write(b"changed fixture revision")
    identity = str(uuid4())
    response = state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm", json={"request_id": identity} if use_request_identity else {})
    assert response.status_code == 409, response.text
    assert state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == before
    assert state.client.get(state.base_url + f"/assistant/edit-requests/{payload['request_id']}").json()["status"] == "recorded"


def test_other_workspace_and_task_do_not_resolve_original_request(material_edit_state, monkeypatch):
    state = material_edit_state
    payload = bound(state, **replace_action(state))
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()["proposal"]["id"]
    assert state.client.post(f"/api/creation-tasks/another-task/edits/{proposal_id}/confirm", json={"request_id": str(uuid4())}).status_code == 404
    monkeypatch.setattr(main, "current_workspace_id", lambda _: "another-workspace")
    assert state.client.get(state.base_url + "/assistant/edit-capabilities").status_code == 404
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload).status_code == 404
    assert state.client.get(state.base_url + f"/edits/{proposal_id}/result").status_code == 404


def test_typed_subtitle_update_and_apply_share_the_current_version(material_edit_state):
    state = _build_linked_state(material_edit_state)
    task = state.production.get_creation_task(state.workspace_id, state.task.id)
    track = next(item for item in state.production.list_subtitle_tracks(state.workspace_id, task.id, timeline_version_id=task.current_timeline_version_id) if item.language == "zh-CN")
    payload = bound(state, operation_type="UpdateSubtitleCue", track_id=track.id,
                    cue_id=track.cues[0].id, text="这是修改后的第一句。")
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()["proposal"]["id"]
    apply = state.client.post(state.base_url + f"/edits/{proposal_id}/confirm", json={"request_id": str(uuid4())})
    assert apply.status_code == 200, apply.text
    updated = state.production.get_creation_task(state.workspace_id, task.id)
    result_track = next(item for item in state.production.list_subtitle_tracks(state.workspace_id, task.id, timeline_version_id=updated.current_timeline_version_id) if item.language == "zh-CN")
    assert result_track.cues[0].text == payload["text"]
    assert result_track.cues[0].start_seconds == track.cues[0].start_seconds
    assert result_track.cues[0].end_seconds == track.cues[0].end_seconds


def test_subtitle_configuration_rejects_mixed_modes(material_edit_state):
    state = material_edit_state
    response = state.client.post(state.base_url + "/edits/exact/preview", json={
        "operation_type": "ConfigureSubtitleTrack", "style": {"font_size": 40}, "target_language": "en-US"})
    assert response.status_code == 422, response.text
    assert "不同动作" in response.text


def test_apply_request_identity_is_not_reusable_for_another_proposal(material_edit_state):
    state = material_edit_state
    first = state.client.post(state.base_url + "/edits/exact/preview", json=bound(state, **replace_action(state)))
    second = state.client.post(state.base_url + "/edits/exact/preview?draft=true", json=bound(state,
        operation_type="ChangeCrop", clip_id=state.clip.id, fit_mode="fill", crop_x=0.2, crop_y=0.5))
    assert first.status_code == second.status_code == 200
    identity = {"request_id": str(uuid4())}
    assert state.client.post(state.base_url + f"/edits/{first.json()['proposal']['id']}/confirm", json=identity).status_code == 200
    version = state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id
    response = state.client.post(state.base_url + f"/edits/{second.json()['proposal']['id']}/confirm", json=identity)
    assert response.status_code == 409 and "不同编辑输入" in response.text
    assert state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == version


def test_manual_undo_redo_require_current_expected_pair_without_assistant_turn(material_edit_state):
    state = material_edit_state
    original = state.production.get_creation_task(state.workspace_id, state.task.id)
    original_version = state.production.get_timeline_version(state.workspace_id, original.current_timeline_version_id)
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=bound(state, **replace_action(state)))
    assert preview.status_code == 200, preview.text
    proposal_id = preview.json()["proposal"]["id"]
    assert state.client.post(state.base_url + f"/edits/{proposal_id}/confirm", json={"request_id": str(uuid4())}).status_code == 200
    task = state.production.get_creation_task(state.workspace_id, state.task.id)
    version = state.production.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    undo = state.base_url + "/timeline/undo"
    stale = state.client.post(undo, json={"expected_timeline_version_id": original_version.id, "expected_timeline_sequence": original_version.sequence})
    assert stale.status_code == 409
    assert state.production.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == version.id
    assert state.client.post(undo, json={"expected_timeline_version_id": version.id}).status_code == 422
    assert state.client.post(undo, json={"expected_timeline_sequence": version.sequence}).status_code == 422
    _other, token = state.members.join(state.members.create_invite_code(), "other history caller")
    expected = {"expected_timeline_version_id": version.id, "expected_timeline_sequence": version.sequence}
    assert state.client.post(undo, json=expected, headers={"X-Local-Member-Token": token}).status_code in {403, 404}
    undone = state.client.post(undo, json=expected)
    assert undone.status_code == 200, undone.text
    task = state.production.get_creation_task(state.workspace_id, state.task.id)
    undo_version = state.production.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    redone = state.client.post(state.base_url + "/timeline/redo", json={
        "expected_timeline_version_id": undo_version.id, "expected_timeline_sequence": undo_version.sequence})
    assert redone.status_code == 200, redone.text


def test_other_member_cannot_read_or_replay_private_typed_edit(material_edit_state):
    state = material_edit_state
    payload = bound(state, **replace_action(state))
    preview = state.client.post(state.base_url + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    _member, token = state.members.join(state.members.create_invite_code(), "other")
    headers = {"X-Local-Member-Token": token}
    assert state.client.get(state.base_url + "/assistant/edit-capabilities", headers=headers).status_code in {403, 404}
    assert state.client.post(state.base_url + "/edits/exact/preview", json=payload, headers=headers).status_code in {403, 404}
    proposal = preview.json()["proposal"]
    assert state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm", json={"request_id": str(uuid4())}, headers=headers).status_code in {403, 404}
