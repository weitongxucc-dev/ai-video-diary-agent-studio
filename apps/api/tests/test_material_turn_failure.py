from app.materials.library_routes import MaterialLibraryHTTPAdapter
from datetime import datetime, timezone
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.assistant_pi as pi_module
from assistant_sdk_fixtures import sdk_completed
from app.conversation_repository import ConversationRepository
from app.local_members import LocalMemberService


@pytest.mark.parametrize(
    ("invalid_result", "expected_http_status"),
    [
        ("wrong_scope", 502),
        ("missing_required_tag_fields", 503),
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
    settings = replace(main_module.settings, model_root_path=str(tmp_path / 'models'),
        llm_provider='minimax', llm_api_key='synthetic-material-key', llm_api_key_provider='minimax',
        llm_base_url='https://api.minimaxi.com/v1', llm_model='MiniMax-M3')
    monkeypatch.setattr(main_module, "_workspace_settings_for", lambda _workspace: settings)
    # Only the SDK process/installation and memory-preflight ports are replaced.
    # The real stream projection, material scope gate and turn owner still run.
    monkeypatch.setattr(pi_module, 'pi_runtime_checks', lambda: [])
    monkeypatch.setattr(pi_module, '_pi_executable', lambda: sys.executable)
    module = tmp_path / 'synthetic-sdk.mjs'
    module.write_text('// layout identity only; never executed\n')
    monkeypatch.setattr('app.assistant.runtime_context.sdk_module', lambda _: module)
    monkeypatch.setattr(pi_module, '_read_memory_before_pi', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pi_module, '_record_service', lambda *_args, **_kwargs: None)
    def forbid_process(*_args, **_kwargs):
        pytest.fail('material result boundary must not launch Pi, models or media')
    monkeypatch.setattr(subprocess, 'Popen', forbid_process)
    monkeypatch.setattr(MaterialLibraryHTTPAdapter, "get_production_material", lambda _adapter, _asset_id, _request: SimpleNamespace(
        id=asset_id, updated_at=datetime(2026,10,10,tzinfo=timezone.utc),
        segments=[SimpleNamespace(id=segment_id, asset_id=asset_id, updated_at=datetime(2026,10,10,tzinfo=timezone.utc), start_seconds=0.0, end_seconds=2.0)],
    ))

    def controlled_sdk_stream(command, *, turn_id, env, on_event, **kwargs):
        tool_calls.append(turn_id)
        selection = json.loads(env['AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION'])
        assert {key: selection[key] for key in ('scope', 'assetId', 'segmentId')} == {
            "scope": "segment", "assetId": asset_id, "segmentId": segment_id,
        }
        events = []
        if len(tool_calls) == 1:
            result = {"scope": "segment", "asset_ref": f"asset:{asset_id}",
                      "segment_ref": f"segment:{segment_id}", 'system_tags': [],
                      'action': 'add', 'changed': True}
            if invalid_result == "wrong_scope":
                result.update(scope='asset', segment_ref=None, user_tags=['must-not-be-applied'])
            events.append({'type': 'tool_execution_end', 'toolName': 'update_selected_material_tags',
                           'isError': False, 'result': {'details': result}})
        events.extend([{'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': 'stop',
            'content': [{'type': 'text', 'text': '受控工具已返回' if len(tool_calls) == 1 else '下一轮已完成'}]}},
            {'type': 'agent_settled'}])
        assert Path(command[-1]).name == 'host.mjs'
        return sdk_completed(command, 0, stdout='\n'.join(json.dumps(event) for event in events), stderr='')

    monkeypatch.setattr(pi_module, '_run_pi_streaming', controlled_sdk_stream)
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
    if invalid_result == 'wrong_scope':
        assert '当前选择核对' in rejected.text
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
    assert replay.status_code == 503, replay.text
    assert replay.json()['detail'] == turns[0]['result']['message']
    assert len(repository.list_turns(conversation_id)) == 1
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
    assert settings.llm_api_key not in json.dumps(turns, ensure_ascii=False)
    for config in tmp_path.rglob('models.json'):
        assert settings.llm_api_key not in config.read_text()
