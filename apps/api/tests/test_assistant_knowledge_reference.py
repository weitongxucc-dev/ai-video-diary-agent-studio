from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
import app.main as main
from app.source_repository import SourceRepository
from app.sources import KnowledgeItem
from app.knowledge_files import KnowledgeSection, KnowledgeParameter
from app.local_members import LocalMemberService
from app.conversation_repository import ConversationRepository


@pytest.fixture
def reference_client(tmp_path, monkeypatch, request):
    metadata_only = request.node.originalname == "test_fixed_knowledge_reference_rechecks_current_grant"
    forbidden_calls = []
    if metadata_only:
        import subprocess
        import wave
        from app.auth import SupabaseAuthContext
        from app.config import Settings
        from app.device_runtime import DeviceRuntime
        from app.device_scheduler import DeviceScheduler
        from app.knowledge_file_store import KnowledgeFileStore
        from app.production import ProductionRepository

        def forbidden(*args, **kwargs):
            forbidden_calls.append("execution-or-original-file-read")
            pytest.fail("reference metadata forbids execution, admission, media and original file bytes")

        monkeypatch.setattr(subprocess, "Popen", forbidden)
        monkeypatch.setattr(wave, "open", forbidden)
        for name in ("start", "run_once", "wake"):
            monkeypatch.setattr(DeviceRuntime, name, forbidden)
        monkeypatch.setattr(DeviceScheduler, "pick_and_grant", forbidden)
        monkeypatch.setattr(KnowledgeFileStore, "read", forbidden)
        for name in ("_creation_runtime", "run_task_turn", "run_plan_turn", "start_assistant_continuation_driver"):
            monkeypatch.setattr(main, name, forbidden)
        monkeypatch.setattr(main.montage_renderer, "render", forbidden)
        monkeypatch.setattr(main.narration_generator, "generate_for_creation_task", forbidden)
        settings = Settings(database_path=str(tmp_path / "production.sqlite3"),
            video_inbox_path=str(tmp_path / "library"), video_output_path=str(tmp_path / "output"),
            model_root_path=str(tmp_path / "models"), temporary_root_path=str(tmp_path / "temporary"),
            draft_output_path=str(tmp_path / "drafts"), jianying_draft_root_path=str(tmp_path / "jianying"),
            obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
            local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
            native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
        monkeypatch.setattr(main, "settings", settings)
        monkeypatch.setattr(main, "auth_context", SupabaseAuthContext(settings))
        monkeypatch.setattr(main, "production_repo", ProductionRepository(tmp_path / "production.sqlite3", tmp_path / "library"))
        monkeypatch.setattr(main, "device_runtime", None)
    members = LocalMemberService(tmp_path / "members.sqlite3")
    owner, token = members.whoami(None)
    _other, other_token = members.join(members.create_invite_code(), "其他成员")
    repo = SourceRepository(tmp_path / "knowledge.sqlite3")
    repo.create_knowledge_item_owned(KnowledgeItem(
        id="knowledge-real", workspace_id="qa", content_type="file", title="第一版资料", body="第一版正文",
        file_reference="/private/never-for-model.pdf", authorization="user-provided", ownership="user-owned",
        source_item_id=None, version="1", sections=(KnowledgeSection("第1段", "第一段", "第一段事实"),),
        parameters=(KnowledgeParameter("重量", "20", "kg", "第1段"),),
    ), verified_user_id=owner.member_id)
    repo.edit_knowledge_item("qa", "knowledge-real", expected_version="1", title="第二版资料", body="新正文",
                             verified_user_id=owner.member_id)
    conversations = ConversationRepository(tmp_path / "conversation.sqlite3")
    monkeypatch.setattr(main, "source_repository", repo)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "local_members_service", members)
    monkeypatch.setattr(main, "current_workspace_id", lambda request: request.headers.get("x-test-workspace", "qa"))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(effective_llm_provider="synthetic", effective_llm_model="fixture", effective_llm_api_key=None))
    client = TestClient(main.app)
    if metadata_only:
        import sqlite3

        def metadata_snapshot():
            databases = {}
            for path in sorted(tmp_path.rglob("*")):
                if not path.is_file() or path.suffix not in {".sqlite3", ".sqlite", ".db"}:
                    continue
                with sqlite3.connect(path) as connection:
                    tables = [row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                    databases[str(path.relative_to(tmp_path))] = {
                        name: sorted((tuple(row) for row in connection.execute(
                            'SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr) for name in tables}
            tree = {str(item.relative_to(tmp_path)): item.read_bytes() if item.is_file() else None
                    for item in tmp_path.rglob('*')}
            return databases, tree

        def verify_no_execution():
            assert forbidden_calls == []
            client.close()

        client.metadata_snapshot = metadata_snapshot
        request.addfinalizer(verify_no_execution)
    return client, {"X-Local-Member-Token":token}, {"X-Local-Member-Token":other_token}, conversations


def test_knowledge_reference_reads_exact_old_version_locator_without_path(reference_client):
    client, headers, other, _ = reference_client
    path = "/api/assistant/knowledge-references/knowledge-real"
    result = client.get(path, params={"version":"1", "locator":"第1段"}, headers=headers)
    assert result.status_code == 200
    body = result.json()
    assert body["version"] == "1" and body["title"] == "第一版资料"
    assert body["body"] is None and body["sections"][0]["text"] == "第一段事实"
    assert body["parameters"][0]["value"] == "20"
    assert "file_reference" not in body and "/private/" not in result.text
    for params in ({"version":"1","locator":"第2段"}, {"version":"nonexistent"}):
        assert client.get(path,params=params,headers=headers).status_code == 404
    assert client.get(path,params={"version":"1"},headers=other).status_code == 404
    assert client.get(path,params={"version":"1"},headers={**headers,"x-test-workspace":"another"}).status_code == 404


def test_knowledge_reference_is_frozen_on_turn_and_passed_to_runtime(reference_client, monkeypatch):
    client, headers, _, conversations = reference_client
    current = client.post("/api/assistant/conversations/current",json={"scope_kind":"home","scope_id":"home"},headers=headers).json()
    calls = []
    def run(_settings, **kwargs):
        calls.append(kwargs["knowledge_reference"])
        return "引用第一版资料。", None
    monkeypatch.setattr(main,"run_plan_turn",run)
    reference = {"knowledge_item_id":"knowledge-real","version":"1","locator":"第1段"}
    payload = {"conversation_id":current["id"],"message":"核对这段资料", "client_request_id":str(uuid4()),"expected_draft_version":1,"selected_knowledge_reference":reference}
    assert client.patch(f"/api/assistant/conversations/{current['id']}",json={"draft_text":payload["message"],"expected_draft_version":0},headers=headers).status_code == 200
    invalid = {**payload, "selected_knowledge_reference":{**reference,"locator":"第2段"}}
    assert client.post("/api/assistant/plan/turn",json=invalid,headers=headers).status_code == 404
    assert conversations.list_turns(current["id"]) == []
    response = client.post("/api/assistant/plan/turn",json=payload,headers=headers)
    assert response.status_code == 200
    snapshot = conversations.request_context(payload["client_request_id"], current["id"], "qa", main.local_members_service.resolve_member(headers["X-Local-Member-Token"]).member_id)["target_snapshot"]["knowledge_reference"]
    assert snapshot == calls[0] and snapshot["version"] == "1" and snapshot["locator"] == "第1段"
    assert "body" not in snapshot and "file_reference" not in snapshot
    assert client.post("/api/assistant/plan/turn",json=payload,headers=headers).status_code == 200
    assert len(calls) == 1


def test_fixed_knowledge_reference_rechecks_current_grant(reference_client):
    client, headers, _, _ = reference_client
    owner_id = main.local_members_service.resolve_member(headers["X-Local-Member-Token"]).member_id
    grant = main.source_repository.grant_knowledge_item("qa", "knowledge-real", "target", user_id=owner_id)
    target_headers = {**headers,"x-test-workspace":"target"}
    path = "/api/assistant/knowledge-references/knowledge-real?version=1&locator=%E7%AC%AC1%E6%AE%B5"
    before_read = client.metadata_snapshot()  # The grant is an expected write, excluded from reads.
    assert set(before_read[0]) == {"production.sqlite3", "members.sqlite3", "knowledge.sqlite3", "conversation.sqlite3"}
    result = client.get(path,headers=target_headers)
    assert result.status_code == 200
    body = result.json()
    assert body["version"] == "1" and body["title"] == "第一版资料" and body["locator"] == "第1段"
    assert body["body"] is None
    assert body["sections"] == [{"locator": "第1段", "title": "第一段", "text": "第一段事实"}]
    assert body["parameters"] == [{"locator": "第1段", "key": "重量", "value": "20", "unit": "kg"}]
    assert "file_reference" not in body and "/private/never-for-model.pdf" not in result.text
    assert client.metadata_snapshot() == before_read
    main.source_repository.revoke_knowledge_grant("qa", "knowledge-real", grant.id, user_id=owner_id)
    after_revoke = client.metadata_snapshot()  # Revocation is another expected write.
    denied = client.get(path,headers=target_headers)
    assert denied.status_code == 404
    assert denied.json()["detail"] == "所引用知识版本不存在或当前不可访问。"
    for kind in ("person", "voice"):
        missing = client.get(f"/api/assistant/profile-references/{kind}/missing?version=missing",headers=headers)
        assert missing.status_code == 404 and missing.json()["detail"] == "所引用档案版本不存在或当前不可访问。"
    assert client.get("/api/assistant/profile-references/unknown/missing?version=missing",headers=headers).status_code == 422
    assert client.metadata_snapshot() == after_revoke


def test_conversation_reference_persists_exact_version_and_cancellation(reference_client):
    client,headers,other,_=reference_client
    conversation=client.post("/api/assistant/conversations/current",json={"scope_kind":"home","scope_id":"home"},headers=headers).json()
    path=f"/api/assistant/conversations/{conversation['id']}/reference"
    reference={"kind":"knowledge","knowledge_item_id":"knowledge-real","version":"1","locator":"第1段","label":"第一版资料"}
    result=client.patch(path,json={"selected_reference":reference,"expected_reference_version":0},headers=headers)
    assert result.status_code==200 and result.json()["selected_reference"]==reference
    assert result.json()["reference_version"]==1
    assert client.get(path,headers=headers).json()["selected_reference"]==reference
    assert client.get(path,headers=other).status_code==404
    assert client.get(path,headers={**headers,"x-test-workspace":"another"}).status_code==404
    cleared=client.patch(path,json={"selected_reference":None,"expected_reference_version":1},headers=headers)
    assert cleared.status_code==200 and cleared.json()["selected_reference"] is None
    assert client.patch(path,json={"selected_reference":reference,"expected_reference_version":1},headers=headers).status_code==409
    assert client.get(path,headers=headers).json()["selected_reference"] is None


def test_restored_reference_rechecks_revoked_grant_without_rebinding(reference_client):
    client,headers,_,_=reference_client
    owner=main.local_members_service.resolve_member(headers["X-Local-Member-Token"]).member_id
    grant=main.source_repository.grant_knowledge_item("qa","knowledge-real","target",user_id=owner)
    target={**headers,"x-test-workspace":"target"}
    conversation=client.post("/api/assistant/conversations/current",json={"scope_kind":"home","scope_id":"home"},headers=target).json()
    path=f"/api/assistant/conversations/{conversation['id']}/reference"
    reference={"kind":"knowledge","knowledge_item_id":"knowledge-real","version":"1","label":"第一版资料"}
    assert client.patch(path,json={"selected_reference":reference,"expected_reference_version":0},headers=target).status_code==200
    main.source_repository.revoke_knowledge_grant("qa","knowledge-real",grant.id,user_id=owner)
    response=client.get(path,headers=target).json()
    assert response["status"]=="unavailable" and response["selected_reference"]=={**reference,"locator":None} and response["reason"]
    assert client.patch(path,json={"selected_reference":None,"expected_reference_version":1},headers=target).status_code==200
