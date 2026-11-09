import pytest
from app.materials.library_routes import MaterialLibraryHTTPAdapter
from fastapi.testclient import TestClient
from types import SimpleNamespace
from uuid import uuid4

import app.main as main_module
from app.assistant_pi import ASSISTANT_BUSINESS_TOOLS, AssistantUnavailable
from app.conversation_repository import ConversationRepository
from app.local_members import LocalMemberService


@pytest.fixture(autouse=True)
def assistant_capability_metadata_guard(request, monkeypatch, tmp_path):
    if request.node.originalname != 'test_assistant_capabilities_describes_product_sdk_runtime':
        return None
    import sqlite3
    import subprocess
    import wave
    import httpx
    from PIL import Image
    from app import assistant_pi
    from app.assistant import runtime_checks
    from app.auth import SupabaseAuthContext
    from app.config import Settings
    from app.device_runtime import DeviceRuntime
    from app.device_scheduler import DeviceScheduler

    violations = []

    def forbidden(*args, **kwargs):
        violations.append('actual-pi-runtime-or-media')
        pytest.fail('controlled capability projection forbids actual Pi checks, network, runtime and media')

    # Protect before the original member seed; the original body later supplies its controlled main lambda.
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    monkeypatch.setattr(assistant_pi, 'pi_runtime_checks', forbidden)
    monkeypatch.setattr(assistant_pi, 'runtime_checks', forbidden)
    monkeypatch.setattr(runtime_checks, 'runtime_checks', forbidden)
    monkeypatch.setattr(main_module, 'pi_runtime_checks', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('_creation_runtime', '_build_device_components', '_start_device_runtime',
                 '_device_operation_budget', '_workspace_settings_for', 'run_task_turn',
                 'run_plan_turn', 'start_assistant_continuation_driver'):
        monkeypatch.setattr(main_module, name, forbidden)
    monkeypatch.setattr(main_module, 'montage_renderer', SimpleNamespace(render=forbidden))
    monkeypatch.setattr(main_module, 'narration_generator', SimpleNamespace(generate_for_creation_task=forbidden))
    settings = Settings(database_path=str(tmp_path / 'unused.sqlite'),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'outputs'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main_module, 'settings', settings)
    monkeypatch.setattr(main_module, 'auth_context', SupabaseAuthContext(settings))
    for name in ('device_runtime', 'automation_runtime', 'production_repo', 'conversation_repo',
                 'source_repository', 'artifact_store', 'local_members_service', 'vault'):
        monkeypatch.setattr(main_module, name, None)

    def snapshot():
        databases = {}
        for path in tmp_path.rglob('*'):
            if path.is_file() and path.suffix in {'.sqlite', '.sqlite3', '.db'}:
                with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as connection:
                    tables = [row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                    databases[str(path.relative_to(tmp_path))] = {
                        name: sorted(connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(), key=repr)
                        for name in tables}
        return databases, {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                           for path in tmp_path.rglob('*')}

    def prepare():
        prepared = snapshot()  # Original member/schema and controlled monkeypatch preparation is complete.
        assert set(prepared[0]) == {'capability-members.sqlite3'}
        return prepared

    def assert_unchanged(prepared):
        # The original successful controlled projection already ran; this refusal must precede even a check.
        monkeypatch.setattr(main_module, 'pi_runtime_checks', forbidden)
        invalid = TestClient(main_module.app).get('/api/assistant/capabilities',
            headers={'X-Local-Member-Token': 'invalid-token'})
        assert invalid.status_code == 401
        assert snapshot() == prepared
        assert violations == []

    return SimpleNamespace(prepare=prepare, assert_unchanged=assert_unchanged)


def test_assistant_capabilities_describes_product_sdk_runtime(tmp_path, monkeypatch, assistant_capability_metadata_guard):
    members = LocalMemberService(tmp_path / "capability-members.sqlite3")
    _member, token = members.join(members.create_invite_code(), "成员甲")
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "workspace-one")
    monkeypatch.setattr(main_module, "pi_runtime_checks", lambda: [{
        "id": "pi", "status": "ready", "version": "1.5.0", "reason": "隔离版本检查已通过。",
    }])
    prepared_metadata = assistant_capability_metadata_guard.prepare()
    response = TestClient(main_module.app).get(
        "/api/assistant/capabilities", headers={"X-Local-Member-Token": token},
    )
    assert response.status_code == 200
    capability = response.json()["pi_tui_commands"]
    assert capability["available"] is False
    assert capability["commands"] == []
    assert "inventory_version" not in capability
    assert response.json()["runtime"] == {
        "owner": "pi-coding-agent-sdk", "version": "1.5.0", "status": "ready",
        "reason": "隔离版本检查已通过。",
    }
    assert response.json()["skills"]["names"] == ["source-speech", "narration-materials", "local-edit"]
    assert response.json()["prompt_templates"]["names"] == ["review-result"]
    assert response.json()["business_tools"] == {
        scope: list(tools) for scope, tools in ASSISTANT_BUSINESS_TOOLS.items()
    }
    assistant_capability_metadata_guard.assert_unchanged(prepared_metadata)


def test_conversation_resume_selects_persistent_current_for_trusted_member(tmp_path, monkeypatch):
    workspace = {"id": "workspace-one"}
    repo = ConversationRepository(tmp_path / "assistant.sqlite3")
    members = LocalMemberService(tmp_path / "members.sqlite3")
    _member, token = members.join(members.create_invite_code(), "成员甲")
    other, other_token = members.join(members.create_invite_code(), "成员乙")
    monkeypatch.setattr(main_module, "conversation_repo", repo)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: workspace["id"])
    client = TestClient(main_module.app)

    scope = {"scope_kind": "home", "scope_id": "home"}
    assert client.post("/api/assistant/conversations/current", json=scope).status_code == 403
    headers = {"X-Local-Member-Token": token}
    first = client.post("/api/assistant/conversations/current", json=scope, headers=headers)
    assert first.status_code == 200
    old_id = first.json()["id"]
    second = client.post("/api/assistant/conversations", json={**scope, "title": "新对话"}, headers=headers)
    assert second.status_code == 200
    new_id = second.json()["id"]
    client.patch(f"/api/assistant/conversations/{old_id}", json={"draft_text": "迟到的草稿保存"}, headers=headers)
    assert client.post("/api/assistant/conversations/current", json=scope, headers=headers).json()["id"] == new_id

    assert client.post(f"/api/assistant/conversations/{old_id}/resume", headers=headers).json()["id"] == old_id
    repo_after_restart = ConversationRepository(tmp_path / "assistant.sqlite3")
    assert repo_after_restart.current_or_create("workspace-one", members.resolve_member(token).member_id,
                                                "home", "home")["id"] == old_id
    assert client.get(f"/api/assistant/conversations/{old_id}", headers={"X-Local-Member-Token": other_token}).status_code == 404

    workspace["id"] = "workspace-two"
    assert client.get(f"/api/assistant/conversations/{old_id}", headers=headers).status_code == 404


def test_unavailable_assistant_turn_retains_partial_reply_and_persisted_failure(tmp_path, monkeypatch):
    repo = ConversationRepository(tmp_path / "failed-assistant.sqlite3")
    members = LocalMemberService(tmp_path / "failed-members.sqlite3")
    _member, token = members.join(members.create_invite_code(), "成员甲")
    monkeypatch.setattr(main_module, "conversation_repo", repo)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "workspace-one")
    monkeypatch.setattr(main_module, "_workspace_settings_for", lambda _workspace_id: SimpleNamespace(
        effective_llm_provider="test-provider", effective_llm_model="test-model",
        effective_llm_api_key=None,
    ))
    conversation = repo.current_or_create("workspace-one", members.resolve_member(token).member_id, "home", "home")

    calls = []
    def unavailable(_settings, *, turn_id, **_kwargs):
        calls.append(turn_id)
        repo.append_event(turn_id, {"type": "text_delta", "delta": "已读到部分上下文。"})
        raise AssistantUnavailable("文字模型当前不可用；请检查设置。", cause="runtime_missing")

    monkeypatch.setattr(main_module, "run_plan_turn", unavailable)
    client = TestClient(main_module.app)
    headers = {"X-Local-Member-Token": token}
    message = "先检查当前方案"
    saved = client.patch(f"/api/assistant/conversations/{conversation['id']}", headers=headers, json={
        "draft_text": message, "expected_draft_version": conversation["draft_version"],
    })
    assert saved.status_code == 200
    request = {
        "conversation_id": conversation["id"], "message": message,
        "client_request_id": str(uuid4()), "expected_draft_version": saved.json()["draft_version"],
    }
    response = client.post("/api/assistant/plan/turn", headers=headers, json=request)
    assert response.status_code == 503
    saved = ConversationRepository(tmp_path / "failed-assistant.sqlite3").list_turns(conversation["id"])[0]
    assert saved["status"] == "failed"
    assert saved["assistant_text"] == "已读到部分上下文。"
    assert saved["result"] == {"kind": "failure", "message": "文字模型当前不可用；请检查设置。"}
    replay = client.post("/api/assistant/plan/turn", headers=headers, json=request)
    assert replay.status_code == 503
    assert len(calls) == 1  # A failed durable turn is not automatically re-run on HTTP retry.


def test_assistant_draft_cas_and_unpersisted_recovery_preserve_newer_text(tmp_path, monkeypatch):
    from test_production_foundation import edit_adoption_no_execution
    forbidden = edit_adoption_no_execution(monkeypatch)
    monkeypatch.setattr(main_module, "_creation_task_script_generator_for", forbidden)
    repo = ConversationRepository(tmp_path / "assistant.sqlite3")
    members = LocalMemberService(tmp_path / "members.sqlite3")
    _member, token = members.join(members.create_invite_code(), "成员甲")
    monkeypatch.setattr(main_module, "conversation_repo", repo)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "workspace-one")
    headers = {"X-Local-Member-Token": token}
    client = TestClient(main_module.app)
    conversation = client.post("/api/assistant/conversations/current", headers=headers, json={
        "scope_kind": "home", "scope_id": "home",
    }).json()
    path = f"/api/assistant/conversations/{conversation['id']}"
    missing_version = client.patch(path, headers=headers, json={"draft_text": "第一句"})
    assert missing_version.status_code == 422
    saved = client.patch(path, headers=headers, json={
        "draft_text": "需要恢复的原句", "expected_draft_version": conversation["draft_version"],
    })
    assert saved.status_code == 200 and saved.json()["draft_version"] == 1
    # A stale draft must not independently change the title that follows it.
    rejected = client.patch(path, headers=headers, json={
        "draft_text": "不能覆盖原句", "expected_draft_version": 0,
        "title": "不能提前保存的名称",
    })
    assert rejected.status_code == 409
    unchanged = client.get(path, headers=headers).json()
    assert unchanged["draft_text"] == saved.json()["draft_text"]
    assert unchanged["draft_version"] == saved.json()["draft_version"]
    assert unchanged["title"] == saved.json()["title"]

    request_id = str(uuid4())
    restore_payload = {
        "client_request_id": request_id, "user_message": "需要恢复的原句",
        "expected_draft_version": saved.json()["draft_version"],
    }
    restored = client.post(path + "/restore-unpersisted-turn", headers=headers, json=restore_payload)
    assert restored.status_code == 200
    assert restored.json()["status"] == "restored"
    assert restored.json()["conversation"]["draft_text"] == "需要恢复的原句"
    assert restored.json()["conversation"]["draft_version"] == 2
    duplicate_restore = client.post(path + "/restore-unpersisted-turn", headers=headers, json=restore_payload)
    assert duplicate_restore.status_code == 200
    assert duplicate_restore.json()["status"] == "already_recovered"
    assert duplicate_restore.json()["conversation"]["draft_version"] == 2

    newer = client.patch(path, headers=headers, json={
        "draft_text": "后来输入的新句", "expected_draft_version": 2,
    })
    assert newer.status_code == 200 and newer.json()["draft_version"] == 3
    stale_restore = client.post(path + "/restore-unpersisted-turn", headers=headers, json={
        "client_request_id": str(uuid4()), "user_message": "需要恢复的原句", "expected_draft_version": 2,
    })
    assert stale_restore.status_code == 409
    after = client.get(path, headers=headers)
    assert after.status_code == 200
    assert after.json()["draft_text"] == "后来输入的新句"
    assert after.json()["draft_version"] == 3


def test_material_library_turn_without_selection_is_read_only_and_replayed_once(tmp_path, monkeypatch):
    repo = ConversationRepository(tmp_path / "assistant.sqlite3")
    members = LocalMemberService(tmp_path / "members.sqlite3")
    _member, token = members.join(members.create_invite_code(), "成员甲")
    monkeypatch.setattr(main_module, "conversation_repo", repo)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "workspace-one")
    monkeypatch.setattr(main_module, "_workspace_settings_for", lambda _workspace_id: SimpleNamespace(
        effective_llm_provider="test-provider", effective_llm_model="test-model",
        effective_llm_api_key=None,
    ))
    monkeypatch.setattr(MaterialLibraryHTTPAdapter, "get_production_material", lambda _adapter, *_args, **_kwargs:
                        (_ for _ in ()).throw(AssertionError("No material may be guessed without selection")))
    calls = []
    monkeypatch.setattr(main_module, "run_plan_turn", lambda _settings, **kwargs:
                        (calls.append(kwargs) or ("素材库可查询 3 项。", None)))
    headers = {"X-Local-Member-Token": token}
    client = TestClient(main_module.app)
    created = client.post("/api/assistant/conversations/current", headers=headers, json={
        "scope_kind": "material", "scope_id": "library",
    })
    assert created.status_code == 200
    message = "查看素材库状态"
    saved = client.patch(f"/api/assistant/conversations/{created.json()['id']}", headers=headers, json={
        "draft_text": message, "expected_draft_version": created.json()["draft_version"],
    })
    request = {
        "conversation_id": created.json()["id"], "message": message, "selection": None,
        "client_request_id": str(uuid4()), "expected_draft_version": saved.json()["draft_version"],
    }
    response = client.post("/api/assistant/material/turn", headers=headers, json=request)
    assert response.status_code == 200, response.text
    assert response.json()["draft_version"] == saved.json()["draft_version"] + 1
    assert calls[0]["material_selection"] is None
    replay = client.post("/api/assistant/material/turn", headers=headers, json=request)
    assert replay.status_code == 200 and replay.json() == response.json()
    assert len(calls) == 1
    indexed = client.get("/api/assistant/conversations?scope_kind=material&scope_id=library", headers=headers)
    assert indexed.status_code == 200
    assert [item["id"] for item in indexed.json()["items"]] == [created.json()["id"]]
