"""Assistant edits through real isolated repositories; no model or production data."""
from dataclasses import replace
from pathlib import Path

import pytest

import app.main as main
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from test_material_edit_qualification import material_edit_state  # noqa: F401
from test_montage_pipeline import person_render_edit_state  # noqa: F401
from test_linked_timeline_reorder import _build_linked_state, _cues, _plan
from test_task_assistant_timeline_snapshot import task_client  # noqa: F401


@pytest.fixture
def storyboard(person_render_edit_state):
    state = person_render_edit_state
    source = state.montage.clips[1]
    state.montage = replace(state.montage, status="ready", output_path=None, subtitle_path=None,
        clips=[replace(source, id=f"clip-{index + 1}", timeline_start_seconds=start,
            timeline_end_seconds=end, source_start_seconds=4, source_end_seconds=4 + end - start)
            for index, (start, end) in enumerate(((0, 2), (2, 5), (5, 8)))])
    state.production.save_creation_task_montage_plan(state.workspace_id, state.task.id, state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    main._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    state.base_url = f"/api/creation-tasks/{state.task.id}"
    return state


def preview(state, command, **kwargs):
    return state.client.post(state.base_url + "/edits/preview", json={
        "raw_command": command, "context_stage": "montage", "selected_object_refs": [], **kwargs})


def current(state):
    return state.client.get(state.base_url).json()


@pytest.mark.parametrize("command,order", [
    ("1↔2", ["clip-2", "clip-1", "clip-3"]),
    ("把第1个镜头和第3个镜头交换", ["clip-3", "clip-2", "clip-1"]),
    ("交换第一和第三个镜头，保持总时长不变", ["clip-3", "clip-2", "clip-1"]),
    ("把第1个镜头移到第3个位置", ["clip-2", "clip-3", "clip-1"]),
    ("镜头1和镜头2位置互换", ["clip-2", "clip-1", "clip-3"]),
    ("镜头1和镜头2位置互换，只生成修改提案", ["clip-2", "clip-1", "clip-3"]),
    ("那你把镜头1和镜头2的位置互换一下", ["clip-2", "clip-1", "clip-3"]),
    ("请把镜头1和镜头3的位置互换一下吧", ["clip-3", "clip-2", "clip-1"]),
])
def test_indexed_reorder_is_one_version_and_one_undo_without_render(storyboard, command, order):
    state = storyboard
    before = current(state)
    response = preview(state, command)
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    assert proposal["operation"]["operation_type"] == "ReorderTimelineItems"
    assert proposal["operation"]["payload"]["new_order"] == order
    assert _plan(current(state)) == _plan(before)
    confirmed = state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    after = current(state)
    assert [clip["id"] for clip in _plan(after)["clips"]] == order
    assert _plan(after)["duration_seconds"] == _plan(before)["duration_seconds"]
    assert _plan(after)["audio_timeline"] == _plan(before)["audio_timeline"]
    assert after["task"]["timeline_sequence"] == before["task"]["timeline_sequence"] + 1
    assert state.provider.output_cache_keys == []
    assert state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm").status_code == 409
    assert state.client.post(state.base_url + "/timeline/undo", json={}).status_code == 200
    assert _plan(current(state))["clips"] == _plan(before)["clips"]


def test_swap_rechecks_version_and_locks_without_partial_changes(storyboard):
    state = storyboard
    response = preview(state, "1↔3")
    assert response.status_code == 200, response.text
    other = state.client.post(state.base_url + "/edits/exact", json={
        "operation_type": "SetClipLock", "clip_id": "clip-2", "locked": True})
    assert other.status_code == 200, other.text
    before = current(state)
    assert state.client.post(state.base_url + f"/edits/{response.json()['proposal']['id']}/confirm").status_code == 409
    assert preview(state, "1↔3").status_code == 409
    assert _plan(current(state)) == _plan(before)


def test_person_swap_reports_and_applies_existing_narration_subtitle_linkage(person_render_edit_state):
    state = _build_linked_state(person_render_edit_state)
    state.base_url = f"/api/creation-tasks/{state.task.id}"
    before = current(state)
    response = preview(state, "1↔2")
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    assert "配音和字幕按来源一并移动" in proposal["summary"]
    assert state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm").status_code == 200
    after = current(state)
    assert _cues(after, "zh-CN")[0]["text"] == "第二句。"
    assert _plan(after)["audio_timeline"] != _plan(before)["audio_timeline"]
    assert state.client.post(state.base_url + "/timeline/undo", json={}).status_code == 200
    assert _cues(current(state), "zh-CN") == _cues(before, "zh-CN")
    assert _plan(current(state))["audio_timeline"] == _plan(before)["audio_timeline"]


@pytest.mark.parametrize("phase", ["preview", "confirm"])
def test_unequal_material_swap_cannot_move_incidental_presenter_without_explicit_linkage(person_render_edit_state, phase):
    state = person_render_edit_state
    material = state.montage.clips[1]
    person = state.montage.clips[0]
    middle = replace(person, id="clip-middle", source_start_seconds=0, source_end_seconds=3,
        timeline_start_seconds=2, timeline_end_seconds=5, person_render_id=person.person_render_id if phase == "preview" else None)
    plan = replace(state.montage, status="ready", output_path=None, subtitle_path=None, duration_seconds=9,
        clips=[replace(material, id="clip-first", source_start_seconds=4, source_end_seconds=6,
                    timeline_start_seconds=0, timeline_end_seconds=2),
               middle,
               replace(material, id="clip-last", source_start_seconds=4, source_end_seconds=8,
                    timeline_start_seconds=5, timeline_end_seconds=9)])
    state.production.save_creation_task_montage_plan(state.workspace_id, state.task.id, plan.id,
        creation_task_montage_plan_to_payload(plan))
    main._ensure_montage_base_version(state.workspace_id, state.task, plan)
    state.base_url = f"/api/creation-tasks/{state.task.id}"
    if phase == "confirm":
        response = preview(state, "1↔3")
        assert response.status_code == 200, response.text
        proposal_id = response.json()["proposal"]["id"]
        # Recheck fresh person facts as well as version/order: attachment facts
        # can change while the proposal still carries the same timeline pointer.
        middle.person_render_id = person.person_render_id
        state.production.save_creation_task_montage_plan(state.workspace_id, state.task.id, plan.id,
            creation_task_montage_plan_to_payload(plan))
    before = state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)
    versions = state.production.list_timeline_versions(state.workspace_id, state.task.id)
    response = (preview(state, "1↔3") if phase == "preview"
                else state.client.post(state.base_url + f"/edits/{proposal_id}/confirm"))
    assert response.status_code == 409, response.text
    assert "这次换位会移动人物口型镜头" in response.json()["detail"]
    assert "明确联动范围" in response.json()["detail"]
    assert state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id) == before
    assert state.production.list_timeline_versions(state.workspace_id, state.task.id) == versions
    if phase == "preview":
        assert state.production.list_edit_proposals(state.workspace_id, state.task.id) == []
    else:
        assert state.production.get_edit_proposal(state.workspace_id, proposal_id).status == "proposed"


@pytest.mark.parametrize("command", ["把第0个镜头和第2个镜头交换", "1↔4", "1↔2，然后删除配音", "那你把镜头1和镜头2的位置互换一下，然后删除配音"])
def test_invalid_or_extra_reorder_does_not_create_partial_proposal(storyboard, command):
    state = storyboard
    before = current(state)
    assert preview(state, command).status_code == 422
    assert state.production.list_edit_proposals(state.workspace_id, state.task.id) == []
    assert _plan(current(state)) == _plan(before)


@pytest.mark.parametrize("command", ["把这个镜头换成另一个镜头", "给当前选中的镜头换一段其他素材", "替换当前镜头", "把这个镜头换成另一个镜头，保留当前配音", "换一个更好的镜头吧", "你现在就任意帮我换一条"])
def test_generic_replacement_selects_real_candidate_in_unrendered_draft(material_edit_state, monkeypatch, command):
    state = material_edit_state
    monkeypatch.setattr(state.production, "search_material_segments", lambda *_a, **_kw: pytest.fail("typed candidate must not re-search the raw sentence"))
    montage = replace(state.montage, status="ready", output_path=None, subtitle_path=None)
    state.production.save_creation_task_montage_plan(state.workspace_id, state.task.id, montage.id,
        creation_task_montage_plan_to_payload(montage))
    before = current(state)
    response = preview(state, command, selected_object_refs=[f"clip:{state.clip.id}"],
        replacement_asset_id=state.asset.id, replacement_segment_id=state.asset.segments[0].id)
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    assert proposal["operation"]["payload"]["asset_id"] == state.asset.id
    assert _plan(current(state))["clips"] == _plan(before)["clips"]
    confirmed = state.client.post(state.base_url + f"/edits/{proposal['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    after = current(state)
    assert next(clip for clip in _plan(after)["clips"] if clip["id"] == state.clip.id)["asset_id"] == state.asset.id
    assert _plan(after)["duration_seconds"] == _plan(before)["duration_seconds"]
    persisted = state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)
    assert persisted["narration_audio_path"] == montage.narration_audio_path
    assert state.client.post(state.base_url + "/timeline/undo", json={}).status_code == 200
    assert _plan(current(state))["clips"] == _plan(before)["clips"]


@pytest.mark.parametrize("change", ["private", "changed_file", "not_for_automation"])
def test_replacement_candidate_requalified_at_confirmation(material_edit_state, change):
    state = material_edit_state
    response = preview(state, "把这个镜头换成另一个镜头", selected_object_refs=[f"clip:{state.clip.id}"],
        replacement_asset_id=state.asset.id, replacement_segment_id=state.asset.segments[0].id)
    assert response.status_code == 200, response.text
    before = current(state)
    if change == "private":
        member, _ = state.members.join(state.members.create_invite_code(), "另一成员")
        state.asset.owner_user_id = member.member_id
        state.production._save_asset(state.asset)
        state.production.set_asset_visibility(state.workspace_id, state.asset.id, "private")
    elif change == "changed_file":
        with Path(state.asset.path).open("ab") as stream:
            stream.write(b"changed-fixture")
    else:
        state.asset.use_state = "not_for_automation"
        state.production._save_asset(state.asset)
    assert state.client.post(state.base_url + f"/edits/{response.json()['proposal']['id']}/confirm").status_code == 409
    assert _plan(current(state))["clips"] == _plan(before)["clips"]


def test_task_clip_snapshot_binds_both_revisions_and_never_falls_back_from_person(material_edit_state, monkeypatch):
    state = material_edit_state
    monkeypatch.setattr(main.settings, "video_inbox_path", str(Path(state.asset.path).parent), raising=False)
    selection = main._assistant_task_clip_material_selection(state.request, state.task, [f"clip:{state.clip.id}"])
    asset = main.get_production_material(state.clip.asset_id, state.request)
    segment = next(item for item in asset.segments if item.id == state.clip.segment_id)
    assert selection == {"scope": "segment", "asset_id": asset.id, "segment_id": segment.id,
        "expected_updated_at": asset.updated_at.isoformat(), "expected_segment_updated_at": segment.updated_at.isoformat(),
        "start_seconds": segment.start_seconds, "end_seconds": segment.end_seconds}
    assert main._assistant_task_clip_material_selection(state.request, state.task, ["clip:clip-person-target"]) is None
    assert main._assistant_task_clip_material_selection(state.request, state.task, ["clip:missing"]) is None


def test_untyped_replacement_keeps_objective_description_search(material_edit_state, monkeypatch):
    state = material_edit_state
    queries = []
    def no_matching_description(_workspace, **kwargs):
        queries.append(kwargs["query"])
        return []
    monkeypatch.setattr(state.production, "search_material_segments", no_matching_description)
    before = current(state)
    response = preview(state, "把这个镜头换成完全不存在的景象", selected_object_refs=[f"clip:{state.clip.id}"])
    assert response.status_code == 409, response.text
    assert queries == ["完全不存在的景象"]
    assert state.production.list_edit_proposals(state.workspace_id, state.task.id) == []
    assert _plan(current(state)) == _plan(before)


@pytest.mark.parametrize("command", ["将字幕字号改到40", "把这个镜头换成/tmp/forbidden.mp4"])
def test_typed_replacement_cannot_override_other_action_or_path(material_edit_state, command):
    state = material_edit_state
    before = current(state)
    response = preview(state, command, selected_object_refs=[f"clip:{state.clip.id}"],
        replacement_asset_id=state.asset.id, replacement_segment_id=state.asset.segments[0].id)
    assert response.status_code == 409, response.text
    assert state.production.list_edit_proposals(state.workspace_id, state.task.id) == []
    assert _plan(current(state)) == _plan(before)


def test_automatic_candidate_qualification_precedes_twenty_four_item_cap(material_edit_state, monkeypatch):
    state = material_edit_state
    asset = state.asset
    blocked = [replace(asset, id=f"blocked-{index}", use_state="not_for_automation",
        segments=[replace(asset.segments[0], asset_id=f"blocked-{index}", id=f"blocked-segment-{index}")])
        for index in range(24)]
    # The final eligible source is deliberately behind 24 equally ranked
    # manual-only sources. Scope/qualification uses actual file metadata.
    legal = replace(asset, id="last-legal", segments=[replace(asset.segments[0], asset_id="last-legal", id="legal-segment")])
    monkeypatch.setattr(state.production, "list_assets", lambda *_a, **_kw: [*blocked, legal])
    route = state.base_url + f"/montage/clips/{state.clip.id}/candidates"
    manual = state.client.get(route)
    automatic = state.client.get(route + "?for_automation=true")
    assert manual.status_code == automatic.status_code == 200
    assert len(manual.json()) == 24
    assert {item["asset_id"] for item in manual.json()} == {item.id for item in blocked}
    assert [item["asset_id"] for item in automatic.json()] == [legal.id]


@pytest.mark.parametrize("usage_role,length,expected", [
    ("b_roll", 4, False), ("b_roll", 8, True), ("a_roll", 8, False),
])
def test_candidate_source_span_and_role_follow_existing_exact_speed_invariant(material_edit_state, monkeypatch, usage_role, length, expected):
    state = material_edit_state
    clip = next(item for item in state.montage.clips if item.id == state.clip.id)
    clip.source_start_seconds, clip.source_end_seconds = 0, 8
    original = state.production.get_asset(state.workspace_id, clip.asset_id)
    original.segments = [replace(item, start_seconds=0, end_seconds=8) if item.id == clip.segment_id else item for item in original.segments]
    state.production._save_asset(original)
    state.production.save_creation_task_montage_plan(state.workspace_id, state.task.id, state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    asset = replace(state.asset, segments=[replace(state.asset.segments[0], usage_role=usage_role, start_seconds=0, end_seconds=length)])
    monkeypatch.setattr(state.production, "list_assets", lambda *_a, **_kw: [asset])
    response = state.client.get(state.base_url + f"/montage/clips/{state.clip.id}/candidates?for_automation=true")
    assert response.status_code == 200, response.text
    assert bool(response.json()) is expected


def test_task_turn_captures_and_passes_service_material_snapshot(task_client, monkeypatch):
    create, _production, conversations, calls, member = task_client
    client, headers, payload = create(0)
    selection = {"scope": "segment", "asset_id": "asset", "segment_id": "segment",
        "expected_updated_at": "2026-10-11T00:00:00+00:00", "expected_segment_updated_at": "2026-10-11T00:00:00+00:00"}
    monkeypatch.setattr(main, "_assistant_turn_scope", lambda *_: ("montage", ["clip:trusted"]))
    monkeypatch.setattr(main, "_assistant_task_clip_material_selection", lambda *_: selection)
    response = client.post("/api/creation-tasks/task/assistant/turn", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    context = conversations.request_context(payload["client_request_id"], payload["conversation_id"], "qa", member)
    assert context["target_snapshot"]["material_selection"] == calls[0]["material_selection"] == selection
    monkeypatch.setattr(main, "_assistant_task_clip_material_selection", lambda *_: pytest.fail("replay must not rebind material revisions"))
    replay = client.post("/api/creation-tasks/task/assistant/turn", headers=headers, json=payload)
    assert replay.status_code == 200, replay.text
    assert len(calls) == 1
