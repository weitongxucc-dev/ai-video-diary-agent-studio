from types import SimpleNamespace
import pytest

from app.assistant.references import AssistantReferenceConflict, normalize_selection, validate_timeline_reference
from app.conversation_repository import ConversationRepository, ConversationReferenceVersionConflict


def test_selection_keeps_one_cas_store_and_survives_restart(tmp_path):
    path = tmp_path / "conversations.sqlite"
    repo = ConversationRepository(path)
    conversation = repo.create("w", "m", "home", "home")
    item = {"kind": "shot", "task_id": "t", "object_id": "c", "version": "v", "label": "镜头1"}
    selection = normalize_selection({"kind": "selection", "items": [item, item]})
    saved = repo.save_reference(conversation["id"], "w", "m", selection, 0)
    assert saved["selected_reference"]["items"] == [item]
    assert ConversationRepository(path).get(conversation["id"], "w", "m")["selected_reference"] == selection
    with pytest.raises(ConversationReferenceVersionConflict):
        repo.save_reference(conversation["id"], "w", "m", None, 0)
    assert repo.get(conversation["id"], "w", "other") is None
    with pytest.raises(AssistantReferenceConflict):
        normalize_selection({"kind": "selection", "items": [item, {**item, "version": "new"}]})


def test_timeline_reference_uses_actual_object_bounds_and_never_rebinds():
    task = SimpleNamespace(id="t", workspace_id="w", current_timeline_version_id="v")
    timeline = SimpleNamespace(id="v", workspace_id="w", task_id="t", snapshot={"montage": {
        "clips": [{"id": "c", "timeline_start_seconds": 2, "timeline_end_seconds": 5}],
    }})
    item = {"kind": "shot", "task_id": "t", "object_id": "c", "version": "v", "label": "镜头1", "start_ms": 2000, "end_ms": 5000}
    assert validate_timeline_reference(item, task=task, timeline=timeline) == item
    for change in ({"version": "old"}, {"task_id": "other"}, {"object_id": "missing"}, {"start_ms": 0}, {"end_ms": 5001}):
        with pytest.raises(AssistantReferenceConflict):
            validate_timeline_reference({**item, **change}, task=task, timeline=timeline)


def test_legacy_narration_reference_uses_the_workbench_audio_graph(tmp_path, monkeypatch):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    import app.main as main
    from test_metadata_edit_use_case import metadata_state

    state = metadata_state(tmp_path, workspace_id="local")
    conversations = ConversationRepository(state.repo.database_path)
    monkeypatch.setattr(main, "production_repo", state.repo)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "local_members_service", state.members)
    monkeypatch.setattr(main, "current_workspace_id", lambda _: "local")

    client = TestClient(main.app, headers={"X-Local-Member-Token": state.token})
    path = f"/api/creation-tasks/{state.task.id}"
    workspace = client.get(path)
    assert workspace.status_code == 200, workspace.text
    track = next(track for track in workspace.json()["montage"]["audio_timeline"]["tracks"]
                 if track["role"] == "narration")
    clip = track["clips"][0]
    assert state.base.snapshot["montage"]["audio_timeline"] is None
    assert clip["gain_db"] == 0

    chat = client.post("/api/assistant/conversations/current", json={
        "scope_kind": "task", "scope_id": state.task.id,
    })
    assert chat.status_code == 200, chat.text
    chat_id = chat.json()["id"]
    ref_path = f"/api/assistant/conversations/{chat_id}/reference"
    selection = {"kind": "audio", "object_id": clip["id"], "task_id": state.task.id,
                 "version": state.base.id, "label": clip["name"], "start_ms": 0, "end_ms": 4000}
    for change in ({"object_id": "missing"}, {"object_id": "audio-original"}, {"end_ms": 4001}):
        rejected = client.patch(ref_path, json={"expected_reference_version": 0,
                                               "selected_reference": {**selection, **change}})
        assert rejected.status_code == 409, rejected.text

    saved = client.patch(ref_path, json={"expected_reference_version": 0,
                                         "selected_reference": {"kind": "selection", "items": [selection]}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["selected_reference"]["items"] == [selection]

    _other, token = state.members.join(state.members.create_invite_code(), "其他成员")
    outsider = TestClient(main.app, headers={"X-Local-Member-Token": token})
    outsider_chat = outsider.post("/api/assistant/conversations/current", json={
        "scope_kind": "home", "scope_id": "home",
    })
    assert outsider_chat.status_code == 200, outsider_chat.text
    private = outsider.patch(f"/api/assistant/conversations/{outsider_chat.json()['id']}/reference",
                             json={"expected_reference_version": 0, "selected_reference": selection})
    assert private.status_code == 404, private.text

    next_version = replace(state.base, id="base-next", parent_version_id=state.base.id, sequence=2)
    state.repo.timeline_records.create_timeline_version(next_version)
    stale = client.patch(ref_path, json={"expected_reference_version": 1, "selected_reference": selection})
    assert stale.status_code == 409, stale.text
    current = client.get(ref_path)
    assert current.json()["reference_version"] == 1
    assert current.json()["selected_reference"]["items"] == [selection]
    assert state.repo.timeline_records.get_timeline_version("local", state.base.id).snapshot == state.base.snapshot
