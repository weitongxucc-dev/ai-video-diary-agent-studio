from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.conversation_repository import ConversationRepository
from app.local_members import LocalMemberService


@pytest.mark.parametrize(
    ("invalid_result", "expected_http_status"),
    [
        ("wrong_scope", 503),
        ("missing_required_tag_fields", 400),
    ],
    ids=["reject-object-scope", "reject-invalid-result-schema"],
)
def test_rejected_material_result_fails_turn_and_allows_a_new_draft(
    tmp_path, monkeypatch, invalid_result: str, expected_http_status: int,
) -> None:
    repository = ConversationRepository(tmp_path / "assistant.sqlite3")
    members = LocalMemberService(tmp_path / "members.sqlite3")
    _member, token = members.join(members.create_invite_code(), "隔离成员")
    workspace_id = "isolated-workspace"
    asset_id, segment_id = "synthetic-asset", "synthetic-segment"
    tool_calls: list[str] = []

    monkeypatch.setattr(main_module, "conversation_repo", repository)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: workspace_id)
    monkeypatch.setattr(main_module, "_workspace_settings_for", lambda _workspace: SimpleNamespace(
        effective_llm_provider="controlled-provider",
        effective_llm_model="controlled-model",
        effective_llm_api_key=None,
    ))
    monkeypatch.setattr(main_module, "get_production_material", lambda _asset_id, _request: SimpleNamespace(
        id=asset_id,
        segments=[SimpleNamespace(id=segment_id, asset_id=asset_id)],
    ))

    def controlled_material_turn(_settings, *, turn_id, **kwargs):
        tool_calls.append(turn_id)
        assert kwargs["selection"] == {
            "scope": "segment", "asset_id": asset_id, "segment_id": segment_id,
        }
        if len(tool_calls) > 1:
            return "下一轮已完成", {"kind": "conversation"}
        result = {"scope": "segment", "asset_ref": f"asset:{asset_id}",
                  "segment_ref": f"segment:{segment_id}"}
        if invalid_result == "wrong_scope":
            result["scope"] = "asset"
            result["segment_ref"] = None
            result["user_tags"] = ["must-not-be-applied"]
        return "受控工具已返回", {"kind": "material_result", "material_result": result}

    monkeypatch.setattr(main_module, "run_material_turn", controlled_material_turn)
    client = TestClient(main_module.app, raise_server_exceptions=False)
    headers = {"X-Local-Member-Token": token}
    created = client.post("/api/assistant/conversations/current", headers=headers, json={
        "scope_kind": "material", "scope_id": f"asset:{asset_id}",
    })
    assert created.status_code == 200, created.text
    conversation_id = created.json()["id"]
    original_message = f"给片段{segment_id}加标签"
    saved = client.patch(f"/api/assistant/conversations/{conversation_id}", headers=headers, json={
        "draft_text": original_message,
        "expected_draft_version": created.json()["draft_version"],
    })
    assert saved.status_code == 200, saved.text
    request = {
        "conversation_id": conversation_id,
        "message": original_message,
        "client_request_id": str(uuid4()),
        "expected_draft_version": saved.json()["draft_version"],
        "selection": {"scope": "segment", "asset_id": asset_id, "segment_id": segment_id},
    }

    rejected = client.post("/api/assistant/material/turn", headers=headers, json=request)

    assert rejected.status_code == expected_http_status, rejected.text
    turns = repository.list_turns(conversation_id)
    assert len(turns) == 1
    assert turns[0]["status"] == "failed"
    assert turns[0]["result"]["kind"] == "failure"
    assert turns[0]["result"]["message"]
    assert turns[0]["user_message"] == original_message
    after_rejection = client.get(f"/api/assistant/conversations/{conversation_id}", headers=headers).json()
    assert after_rejection["draft_text"] == ""
    assert after_rejection["draft_version"] == saved.json()["draft_version"] + 1

    replay = client.post("/api/assistant/material/turn", headers=headers, json=request)
    assert replay.status_code == 200, replay.text
    assert replay.json()["turn_status"] == "failed"
    assert len(tool_calls) == 1

    next_message = "修正后继续发送新的素材要求"
    next_draft = client.patch(f"/api/assistant/conversations/{conversation_id}", headers=headers, json={
        "draft_text": next_message,
        "expected_draft_version": after_rejection["draft_version"],
    })
    assert next_draft.status_code == 200, next_draft.text
    assert next_draft.json()["draft_text"] == next_message
    assert next_draft.json()["draft_version"] == after_rejection["draft_version"] + 1
    continued = client.post("/api/assistant/material/turn", headers=headers, json={
        **request,
        "message": next_message,
        "client_request_id": str(uuid4()),
        "expected_draft_version": next_draft.json()["draft_version"],
    })
    assert continued.status_code == 200, continued.text
    assert continued.json()["turn_status"] == "completed"
    turns = repository.list_turns(conversation_id)
    assert len(turns) == 2 and turns[-1]["status"] == "completed"
    assert turns[0]["user_message"] == original_message
    assert len(tool_calls) == 2
