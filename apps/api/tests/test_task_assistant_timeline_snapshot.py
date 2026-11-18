import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.assistant_pi import AssistantUnavailable
from app.conversation_repository import ConversationRepository
from app.local_members import LocalMemberService
from app.materials.contracts import MaterialAsset
from app.production import ProductionRepository
from app.source_repository import SourceRepository
from app.sources import KnowledgeItem
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import CreationTask, TimelineVersion


@pytest.fixture
def task_client(tmp_path, monkeypatch, request):
    from assistant_turn_control_fixtures import isolate_assistant_turn_control
    isolate_assistant_turn_control(monkeypatch, request)
    from test_production_foundation import edit_adoption_no_execution
    forbidden = edit_adoption_no_execution(monkeypatch)
    monkeypatch.setattr(main, "_creation_task_script_generator_for", forbidden)
    members = LocalMemberService(tmp_path / "conversation.sqlite3")
    member, token = members.join(members.create_invite_code(), "任务成员")
    production = ProductionRepository(tmp_path / "conversation.sqlite3", tmp_path / "library")
    conversations = ConversationRepository(tmp_path / "conversation.sqlite3")
    monkeypatch.setattr(main, "production_repo", production)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "local_members_service", members)
    # Real local identity, Task, Conversation and authorization owners share this
    # isolated store; no actor/verifier or business permission gate is mocked.
    monkeypatch.setattr(main, "LEGACY_WORKSPACE_ID", "qa")
    monkeypatch.setattr(main, "settings", replace(main.settings, database_path=str(production.database_path)))
    monkeypatch.setattr(main, "current_workspace_id", lambda _: "qa")
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="controlled", effective_llm_model="fixture", effective_llm_api_key=None,
    ))
    calls = []

    def run(_settings, **kwargs):
        calls.append(kwargs)
        return "已读取固定任务版本。", None

    monkeypatch.setattr(main, "run_task_turn", run)

    def create(sequence, *, reader_token=token):
        plan = CreationPlan(
            id="plan", workspace_id="qa", goal="版本校验", name="隔离任务", target_count=1,
            language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
            knowledge_references=[], source_references=[], selected_voice=None,
            pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
            material_rule_summary="无素材", model_snapshot=[], confirmation_points=[], blockers=[],
            preflight_fingerprint="fixture",
        )
        task = CreationTask(id="task", workspace_id="qa", plan_id=plan.id,
                            name=plan.name, scenario="knowledge_explanation",
                            stage="narration" if sequence else "topic",
                            confirmed_script_id="script" if sequence else None)
        production.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=member.member_id)
        for number in range(1, sequence + 1):
            production.timeline_records.create_timeline_version(TimelineVersion(
                id=f"timeline-{number}", workspace_id="qa", task_id=task.id,
                parent_version_id=f"timeline-{number - 1}" if number > 1 else None,
                sequence=number, snapshot={}, created_by_operation_id=None,
            ))
        headers = {"X-Local-Member-Token": reader_token}
        client = TestClient(main.app)
        conversation = client.post("/api/assistant/conversations/current", headers=headers,
                                   json={"scope_kind": "home", "scope_id": "home"}).json()
        payload = {
            "conversation_id": conversation["id"], "message": "核对任务，不执行写入。",
            "client_request_id": str(uuid4()), "expected_draft_version": 1,
            "context_stage": task.stage, "expected_timeline_sequence": sequence,
            "expected_timeline_version_id": f"timeline-{sequence}" if sequence else None,
        }
        draft = client.patch(f"/api/assistant/conversations/{conversation['id']}", headers=headers,
                             json={"draft_text": payload["message"], "expected_draft_version": 0})
        assert draft.status_code == 200, draft.text
        return client, headers, payload

    return create, production, conversations, calls, member.member_id


@pytest.mark.parametrize("sequence", [0, 2], ids=["new-task", "persisted-timeline"])
def test_task_turn_uses_repository_sequence_for_guard_snapshot_and_runtime(task_client, sequence):
    create, _, conversations, calls, member_id = task_client
    client, headers, payload = create(sequence)
    response = client.post("/api/creation-tasks/task/assistant/turn", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    version = {"timeline_version_id": payload["expected_timeline_version_id"], "timeline_sequence": sequence}
    assert calls[0]["task_version"] == version
    context = conversations.request_context(payload["client_request_id"], payload["conversation_id"], "qa", member_id)
    assert context["target_snapshot"] == {**version, "profile_reference": None, "knowledge_reference": None}
    assert conversations.list_turns(payload["conversation_id"])[0]["status"] == "completed"


@pytest.mark.parametrize("changes", [
    {"expected_timeline_sequence": 1},
    {"expected_timeline_version_id": "timeline-1"},
])
def test_old_timeline_selection_conflicts_before_creating_turn(task_client, changes):
    create, _, conversations, calls, _ = task_client
    client, headers, payload = create(2)
    response = client.post("/api/creation-tasks/task/assistant/turn", headers=headers,
                           json={**payload, **changes})
    assert response.status_code == 409, response.text
    assert conversations.list_turns(payload["conversation_id"]) == []
    assert calls == []
    assert client.get(f"/api/assistant/conversations/{payload['conversation_id']}",
                      headers=headers).json()["draft_text"] == payload["message"]


@pytest.mark.parametrize("target", ["missing", "another-task"])
def test_unavailable_or_unrelated_persisted_timeline_is_rejected(task_client, target):
    create, production, conversations, calls, member_id = task_client
    client, headers, payload = create(2)
    if target == "another-task":
        other_plan = replace(production.plan_records.get_creation_plan("qa", "plan"), id="other-plan")
        other_task = CreationTask(id="other-task", workspace_id="qa", plan_id=other_plan.id,
                                  name="其他任务", scenario="knowledge_explanation")
        production.plan_confirmation.confirm_creation_plan(other_plan, other_task, verified_owner_user_id=member_id)
        production.timeline_records.create_timeline_version(TimelineVersion(
            id=target, workspace_id="qa", task_id=other_task.id, parent_version_id=None,
            sequence=1, snapshot={}, created_by_operation_id=None,
        ))
    # A corrupt persisted pointer must not authorize a different task's timeline.
    task = production.task_records.get_creation_task("qa", "task")
    task.current_timeline_version_id = target
    with production._connect() as connection:
        connection.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                           (json.dumps(asdict(task), default=str), "qa", task.id))
    response = client.post("/api/creation-tasks/task/assistant/turn", headers=headers, json=payload)
    assert response.status_code == 409, response.text
    assert conversations.list_turns(payload["conversation_id"]) == [] and calls == []


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
def test_task_retry_returns_original_receipt_after_stage_advances(task_client, stream):
    create, production, conversations, calls, _ = task_client
    client, headers, payload = create(0)
    payload["expected_reference_version"] = 0
    endpoint = "/api/creation-tasks/task/assistant/turn"
    first = client.post(endpoint, headers=headers, json=payload)
    assert first.status_code == 200, first.text

    # Another action advanced the work before the original response arrived.
    task = production.task_records.get_creation_task("qa", "task")
    task.stage = "narration"
    task.confirmed_script_id = "script"
    with production._connect() as connection:
        connection.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                           (json.dumps(asdict(task), default=str), "qa", task.id))
    production.timeline_records.create_timeline_version(TimelineVersion(
        id="timeline-after-send", workspace_id="qa", task_id=task.id, parent_version_id=None,
        sequence=1, snapshot={}, created_by_operation_id=None,
    ))

    if stream:
        headers = {**headers, "Accept": "text/event-stream"}
    retry = client.post(endpoint, headers=headers, json=payload)
    assert retry.status_code == 200, retry.text
    if stream:
        events = [json.loads(line.removeprefix("data: ")) for line in retry.text.splitlines()
                  if line.startswith("data: ")]
        assert events[-1]["type"] == "result", events
        assert events[-1]["result"] == first.json()
    else:
        assert retry.json() == first.json()

    for change in ({"context_stage": "narration"}, {"selected_object_refs": ["clip:unrelated"]},
                   {"expected_reference_version": 1}, {"expected_draft_version": 2},
                   {"message": "另一条创作指令。"}):
        changed = client.post(endpoint, headers=headers, json={**payload, **change})
        assert_replay_error(changed, stream, 409)
        assert "此请求身份已用于其他内容或对话范围" in changed.text
    assert len(calls) == 1
    assert len(conversations.list_turns(payload["conversation_id"])) == 1


def assert_replay_error(response, stream, status):
    if stream:
        assert response.status_code == 200, response.text
        events = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
                  if line.startswith("data: ")]
        assert [(event["type"], event.get("status")) for event in events] == [("error", status)], events
    else:
        assert response.status_code == status, response.text


def _share_knowledge(tmp_path, monkeypatch):
    members = main.local_members_service
    author, _ = members.join(members.create_invite_code(), "资料所有者")
    sources = SourceRepository(tmp_path / "sources.sqlite3")
    monkeypatch.setattr(main, "source_repository", sources)
    item = sources.create_knowledge_item_owned(KnowledgeItem(
        id="knowledge", workspace_id="qa", content_type="file", title="共享资料", body="原始内容",
        file_reference=None, authorization="user-provided", ownership="user-owned",
        source_item_id=None, version="1",
    ), verified_user_id=author.member_id)
    sources.set_knowledge_item_visibility("qa", item.id, "shared")
    return sources, item


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("saved_reference", [False, True], ids=["explicit", "conversation-selection"])
def test_task_retry_rechecks_original_knowledge_access(task_client, tmp_path, monkeypatch, stream, saved_reference):
    create, _, conversations, calls, _ = task_client
    client, headers, payload = create(0)
    sources, item = _share_knowledge(tmp_path, monkeypatch)
    reference = {"knowledge_item_id": item.id, "version": item.version}
    endpoint = "/api/creation-tasks/task/assistant/turn"
    selection_endpoint = f"/api/assistant/conversations/{payload['conversation_id']}/reference"
    if saved_reference:
        selected = client.patch(selection_endpoint, headers=headers, json={"expected_reference_version": 0,
            "selected_reference": {**reference, "kind": "knowledge", "label": item.title}})
        assert selected.status_code == 200, selected.text
        payload["expected_reference_version"] = selected.json()["reference_version"]
    else:
        payload.update(selected_knowledge_reference=reference, expected_reference_version=0)
    first = client.post(endpoint, headers=headers, json=payload)
    assert first.status_code == 200, first.text
    turn_id = first.json()["turn_id"]
    original = conversations.get_turn(turn_id)

    # Moving the current selection must not retarget the original request.
    if saved_reference:
        cleared = client.patch(selection_endpoint, headers=headers, json={
            "expected_reference_version": payload["expected_reference_version"], "selected_reference": None})
        assert cleared.status_code == 200, cleared.text
    sources.set_knowledge_item_visibility("qa", item.id, "private")

    if stream:
        headers = {**headers, "Accept": "text/event-stream"}
    denied = client.post(endpoint, headers=headers, json=payload)
    assert_replay_error(denied, stream, 404)
    assert first.json()["reply"] not in denied.text
    assert conversations.get_turn(turn_id) == original
    assert len(calls) == 1


def test_concurrent_task_retry_authorizes_committed_selection_before_sse_events(task_client, tmp_path, monkeypatch):
    create, _, conversations, calls, _ = task_client
    client, headers, payload = create(0)
    sources, item = _share_knowledge(tmp_path, monkeypatch)
    selected = client.patch(f"/api/assistant/conversations/{payload['conversation_id']}/reference",
                            headers=headers, json={"expected_reference_version": 0, "selected_reference": {
        "kind": "knowledge", "label": item.title, "knowledge_item_id": item.id, "version": item.version,
    }})
    assert selected.status_code == 200, selected.text
    payload["expected_reference_version"] = selected.json()["reference_version"]
    endpoint = "/api/creation-tasks/task/assistant/turn"
    retry_read, retry_continue = Event(), Event()
    original_running, original_continue = Event(), Event()
    read = conversations.request_context
    run = main.run_task_turn

    def pause_first_read(*args):
        context = read(*args)
        if not retry_read.is_set():
            assert context is None
            retry_read.set()
            assert retry_continue.wait(5)
        return context

    def hold_original(settings, **kwargs):
        original_running.set()
        assert original_continue.wait(5)
        return run(settings, **kwargs)

    monkeypatch.setattr(conversations, "request_context", pause_first_read)
    monkeypatch.setattr(main, "run_task_turn", hold_original)
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            retry = pool.submit(client.post, endpoint, headers={**headers, "Accept": "text/event-stream"}, json=payload)
            assert retry_read.wait(5)
            original = pool.submit(client.post, endpoint, headers=headers, json=payload)
            assert original_running.wait(5)
            sources.set_knowledge_item_visibility("qa", item.id, "private")
            retry_continue.set()
            assert_replay_error(retry.result(timeout=5), stream=True, status=404)
            turns = conversations.list_turns(payload["conversation_id"])
            assert len(turns) == 1 and turns[0]["status"] == "running"
        finally:
            retry_continue.set()
            original_continue.set()
    first = original.result(timeout=5)
    assert first.status_code == 200 and first.json()["turn_status"] == "completed", first.text
    assert len(calls) == 1


def test_task_retry_distinguishes_material_revision_from_read_access(task_client, tmp_path):
    create, production, _, calls, _ = task_client
    client, headers, payload = create(0)
    members = main.local_members_service
    author, _ = members.join(members.create_invite_code(), "素材所有者")
    asset = production.material_assets.save_asset(MaterialAsset(
        id="asset", workspace_id="qa", file_name="source.mp4", path=str(tmp_path / "source.mp4"),
        size_bytes=0, modified_at=datetime.now(timezone.utc), owner_user_id=author.member_id, visibility="shared",
    ))
    selected = client.patch(f"/api/assistant/conversations/{payload['conversation_id']}/reference",
                            headers=headers, json={"expected_reference_version": 0, "selected_reference": {
        "kind": "material", "label": asset.file_name, "selection": {"scope": "asset", "asset_id": asset.id},
    }})
    assert selected.status_code == 200, selected.text
    payload["expected_reference_version"] = selected.json()["reference_version"]
    endpoint = "/api/creation-tasks/task/assistant/turn"
    first = client.post(endpoint, headers=headers, json=payload)
    assert first.status_code == 200, first.text

    updated = production.material_assets.set_asset_visibility("qa", asset.id, "shared")
    assert updated.updated_at != asset.updated_at
    historical = client.post(endpoint, headers=headers, json=payload)
    assert historical.status_code == 200, historical.text
    assert historical.json() == first.json()
    production.material_assets.set_asset_visibility("qa", asset.id, "private")
    denied = client.post(endpoint, headers=headers, json=payload)
    assert denied.status_code == 404, denied.text
    assert len(calls) == 1


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
def test_task_retry_requires_original_member_and_current_task_access(task_client, stream):
    create, production, conversations, calls, _ = task_client
    members = main.local_members_service
    _, reader_token = members.join(members.create_invite_code(), "共享任务读者")
    _, other_token = members.join(members.create_invite_code(), "另一位读者")
    client, headers, payload = create(0, reader_token=reader_token)
    production.task_records.set_creation_task_visibility("qa", "task", "shared")
    endpoint = "/api/creation-tasks/task/assistant/turn"
    first = client.post(endpoint, headers=headers, json=payload)
    assert first.status_code == 200, first.text
    original = conversations.get_turn(first.json()["turn_id"])

    if stream:
        headers = {**headers, "Accept": "text/event-stream"}
    foreign = client.post(endpoint, headers={**headers, "X-Local-Member-Token": other_token}, json=payload)
    assert_replay_error(foreign, stream, 404)
    production.task_records.set_creation_task_visibility("qa", "task", "private")
    denied = client.post(endpoint, headers=headers, json=payload)
    assert_replay_error(denied, stream, 404)
    assert conversations.get_turn(original["id"]) == original
    assert len(calls) == 1


def test_task_failure_stream_persists_original_message_and_fixed_zero_sequence(task_client, monkeypatch, tmp_path):
    create, _, conversations, calls, member_id = task_client
    client, headers, payload = create(0)

    def unavailable(_settings, **kwargs):
        calls.append(kwargs)
        raise AssistantUnavailable("本机文字连接不可用，请检查配置。", cause="runtime_missing")

    monkeypatch.setattr(main, "run_task_turn", unavailable)
    response = client.post("/api/creation-tasks/task/assistant/turn",
                           headers={**headers, "Accept": "text/event-stream"}, json=payload)
    assert response.status_code == 200
    events = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
              if line.startswith("data: ")]
    started = next(event for event in events if event["type"] == "turn_started")
    assert events[-1]["type"] == "error" and events[-1]["status"] == 503
    turns = ConversationRepository(tmp_path / "conversation.sqlite3").list_turns(payload["conversation_id"])
    assert len(turns) == 1 and turns[0]["id"] == started["turn_id"]
    assert turns[0]["status"] == "failed" and turns[0]["user_message"] == payload["message"]
    assert turns[0]["result"] == {"kind": "failure", "message": "本机文字连接不可用，请检查配置。"}
    context = conversations.request_context(payload["client_request_id"], payload["conversation_id"], "qa", member_id)
    assert context["target_snapshot"]["timeline_sequence"] == 0
    replay = client.post("/api/creation-tasks/task/assistant/turn", headers=headers, json=payload)
    assert replay.status_code == 503 and len(calls) == 1


def test_turn_control_scope_preserves_shutdown_and_restores_original_owner(monkeypatch, request):
    import time
    import subprocess
    import app.assistant_pi as pi
    from app.assistant.turn_control import AssistantStopped
    from assistant_turn_control_fixtures import isolate_assistant_turn_control
    def forbidden(*args, **kwargs):
        pytest.fail("control scope metadata cannot launch or terminate processes")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(pi, "_terminate_process_group", forbidden)
    original, view = pi._turn_control, pi._turn_stop_events
    with monkeypatch.context() as scoped:
        closed = isolate_assistant_turn_control(scoped, request)
        pi.register_turn_control("first-owner")
        assert "first-owner" in pi._turn_stop_events and "first-owner" not in view
        closed.stop_all(deadline_monotonic=time.monotonic()+1)
        with pytest.raises(AssistantStopped):
            pi.register_turn_control("cannot-reopen")
    assert pi._turn_control is original and pi._turn_stop_events is view
    with monkeypatch.context() as scoped:
        fresh = isolate_assistant_turn_control(scoped, request)
        assert fresh is not closed
        pi.register_turn_control("second-owner")
        pi.forget_turn_control("second-owner")
        assert "second-owner" not in pi._turn_stop_events
        with pytest.raises(AssistantStopped):
            closed.register("old-stays-closed")
    assert pi._turn_control is original and pi._turn_stop_events is view
