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
