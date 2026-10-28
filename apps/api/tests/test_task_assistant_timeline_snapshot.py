import json
from dataclasses import asdict, replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.assistant_pi import AssistantUnavailable
from app.conversation_repository import ConversationRepository
from app.local_members import LocalMemberService
from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import CreationTask, TimelineVersion


@pytest.fixture
def task_client(tmp_path, monkeypatch):
    members = LocalMemberService(tmp_path / "members.sqlite3")
    member, token = members.join(members.create_invite_code(), "任务成员")
    production = ProductionRepository(tmp_path / "production.sqlite3", tmp_path / "library")
    conversations = ConversationRepository(tmp_path / "conversation.sqlite3")
    monkeypatch.setattr(main, "production_repo", production)
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "local_members_service", members)
    monkeypatch.setattr(main, "current_workspace_id", lambda _: "qa")
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="controlled", effective_llm_model="fixture", effective_llm_api_key=None,
    ))
    calls = []

    def run(_settings, **kwargs):
        calls.append(kwargs)
        return "已读取固定任务版本。", None

    monkeypatch.setattr(main, "run_task_turn", run)

    def create(sequence):
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
            production.create_timeline_version(TimelineVersion(
                id=f"timeline-{number}", workspace_id="qa", task_id=task.id,
                parent_version_id=f"timeline-{number - 1}" if number > 1 else None,
                sequence=number, snapshot={}, created_by_operation_id=None,
            ))
        headers = {"X-Local-Member-Token": token}
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
        production.create_timeline_version(TimelineVersion(
            id=target, workspace_id="qa", task_id=other_task.id, parent_version_id=None,
            sequence=1, snapshot={}, created_by_operation_id=None,
        ))
    # A corrupt persisted pointer must not authorize a different task's timeline.
    task = production.get_creation_task("qa", "task")
    task.current_timeline_version_id = target
    with production._connect() as connection:
        connection.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                           (json.dumps(asdict(task), default=str), "qa", task.id))
    response = client.post("/api/creation-tasks/task/assistant/turn", headers=headers, json=payload)
    assert response.status_code == 409, response.text
    assert conversations.list_turns(payload["conversation_id"]) == [] and calls == []


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
