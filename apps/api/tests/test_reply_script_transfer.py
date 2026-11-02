from __future__ import annotations
from app.creation_plan_routes import CreationPlanPreflightHttpAdapter
from app.creation_task_text_responses import CreationTaskTextResponseAdapter
from app.creation_task_responses import CreationTaskResponseAdapter

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from starlette.requests import Request

from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan, CreationPlanKnowledgeReference
from app.creation.topic_contracts import (
    TopicCandidate,
    TopicEvidenceReference,
    TopicGenerationRun,
)
from app.creation.contracts import CreationTask
from app.creation.script_contracts import CreationTaskScriptSection
from app import main as main_module
from app.schemas import AssistantReplyScriptTransferRequest, CreationPlanConfirmRequest

WORKSPACE = "workspace-one"
OWNER = "member-one"
REPLY_TEXT = "请制作一段简洁视频，介绍资料里的核心功能。"
REPLY_REFERENCE = {
    "conversation_id": "conversation-one",
    "turn_id": "turn-completed-one",
    "response_digest": hashlib.sha256(REPLY_TEXT.encode("utf-8")).hexdigest(),
}
INPUT_REFERENCES = [{
    "reference_id": "knowledge:knowledge-one:v3",
    "kind": "knowledge",
    "object_id": "knowledge-one",
    "version_or_identity": "v3",
    "title": "当前知识资料",
}]


def make_repo(tmp_path: Path) -> ProductionRepository:
    library = tmp_path / "library"
    library.mkdir()
    return ProductionRepository(tmp_path / "production.sqlite", library)


def make_plan(plan_id: str = "plan-one") -> CreationPlan:
    return CreationPlan(
        id=plan_id,
        workspace_id=WORKSPACE,
        goal="介绍资料的核心功能",
        name="核心功能介绍",
        target_count=1,
        language="zh-CN",
        aspect_ratio="9:16",
        duration_target_seconds=30,
        knowledge_references=[CreationPlanKnowledgeReference(
            id="knowledge-one", version="v3", title="当前知识资料",
            authorization="available", ownership="workspace",
        )],
        source_references=[],
        selected_voice=None,
        pronunciation_references=[],
        approved_asset_count=0,
        approved_clip_count=0,
        material_rule_summary="不使用媒体素材",
        model_snapshot=[],
        confirmation_points=["确认方案"],
        blockers=[],
        preflight_fingerprint="preflight-current-v3",
    )


def confirm_plan_from_reply(
    repo: ProductionRepository,
    *,
    request_id: str = "44444444-4444-4444-8444-444444444444",
    plan_id: str = "plan-one",
    task_id: str = "task-one",
    from_reply: bool = True,
):
    plan = make_plan(plan_id)
    task = CreationTask(
        id=task_id,
        workspace_id=WORKSPACE,
        plan_id=plan.id,
        name=plan.name,
        scenario="knowledge_explanation",
        stage="topic",
    )
    if not from_reply:
        return repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=OWNER)
    return repo.plan_confirmation.confirm_creation_plan_from_reply(
        plan, task, reply_reference=REPLY_REFERENCE, request_id=request_id,
        verified_owner_user_id=OWNER,
    )


def ready_task_for_transfer(tmp_path: Path, *, from_reply: bool = True):
    repo = make_repo(tmp_path)
    plan, task = confirm_plan_from_reply(repo, from_reply=from_reply)
    run = TopicGenerationRun(
        id="topic-run-one",
        workspace_id=WORKSPACE,
        task_id=task.id,
        plan_id=plan.id,
        status="running",
        route_snapshot={"provider": "test-double"},
        prompt_schema_version="topic-test/v1",
        input_references=INPUT_REFERENCES,
    )
    repo.topic_records.start_topic_generation(run)
    topic = TopicCandidate(
        id="topic-one",
        workspace_id=WORKSPACE,
        task_id=task.id,
        run_id=run.id,
        title="核心功能是什么",
        angle="说明用户能得到什么",
        audience="普通创作者",
        outline=["提出需求", "说明结果"],
        score=90,
    )
    evidence = TopicEvidenceReference(
        id="topic-evidence-one",
        workspace_id=WORKSPACE,
        task_id=task.id,
        candidate_id=topic.id,
        kind="knowledge",
        reference_id="knowledge-one",
        version_or_identity="v3",
        title="当前知识资料",
        authorization_or_license="available",
        source_url=None,
    )
    repo.topic_records.complete_topic_generation(run.id, [topic], [evidence])
    repo.topic_records.confirm_topic_candidate(WORKSPACE, task.id, topic.id)
    plan = repo.plan_records.get_creation_plan(WORKSPACE, plan.id)
    task = repo.task_records.get_creation_task(WORKSPACE, task.id)
    return repo, plan, task


def transfer(repo, plan, task, **overrides):
    values = {
        "workspace_id": WORKSPACE,
        "task_id": task.id,
        "request_id": "transfer-request-one",
        "reply_reference": REPLY_REFERENCE,
        "plan_conversion_request_id": "44444444-4444-4444-8444-444444444444",
        "reply_text": REPLY_TEXT,
        "expected_plan_id": plan.id,
        "expected_plan_updated_at": plan.updated_at,
        "expected_topic_id": task.selected_topic_id,
        "expected_task_updated_at": task.updated_at,
        "input_references": INPUT_REFERENCES,
        "script_id": "reply-script-one",
        "run_id": "reply-run-one",
        "title": "对话回复草稿",
        "language": "zh-CN",
        "sections": [CreationTaskScriptSection(
            id="reply-section-one",
            title="回复内容",
            content=REPLY_TEXT,
            visual_prompt="",
            evidence_reference_ids=[],
        )],
        "verified_owner_user_id": OWNER,
    }
    values.update(overrides)
    return repo.script_records.transfer_completed_assistant_reply_to_script(**values)


def test_plan_confirmation_from_reply_is_atomic_idempotent_and_bound(tmp_path: Path) -> None:
    import sqlite3

    repo = make_repo(tmp_path)
    with repo._connect() as connection:
        connection.execute("""CREATE TRIGGER reject_plan_confirmation_event BEFORE INSERT ON task_events
            WHEN NEW.event_type = 'assistant_reply_plan_confirmed'
            BEGIN SELECT RAISE(ABORT, 'isolated plan confirmation event refusal'); END""")
    with pytest.raises(ValueError, match="任务事件 ID 已存在") as refused:
        confirm_plan_from_reply(repo)
    assert isinstance(refused.value.__cause__, sqlite3.IntegrityError)
    assert str(refused.value.__cause__) == "isolated plan confirmation event refusal"
    with repo._connect() as connection:
        for table in ("creation_plans", "creation_tasks", "task_events"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        connection.execute("DROP TRIGGER reject_plan_confirmation_event")
    plan, task = confirm_plan_from_reply(repo)
    retry_plan, retry_task = confirm_plan_from_reply(
        repo, plan_id="generated-new-plan-id", task_id="generated-new-task-id",
    )
    assert (retry_plan.id, retry_task.id) == (plan.id, task.id)
    event = repo.execution_records.list_task_events(WORKSPACE, task.id)
    assert len(event) == 1
    assert event[0].event_type == "assistant_reply_plan_confirmed"
    assert event[0].payload["reply_reference"] == REPLY_REFERENCE
    assert REPLY_TEXT not in str(event[0].payload)

    with pytest.raises(ValueError, match="请求身份已用于不同"):
        repo.plan_confirmation.confirm_creation_plan_from_reply(
            make_plan("changed-plan"),
            CreationTask(
                id="changed-task", workspace_id=WORKSPACE, plan_id="changed-plan",
                name="不同目标", scenario="knowledge_explanation",
            ),
            reply_reference={**REPLY_REFERENCE, "turn_id": "another-turn"},
            request_id="44444444-4444-4444-8444-444444444444",
            verified_owner_user_id=OWNER,
        )
    assert len(repo.execution_records.list_task_events(WORKSPACE, task.id)) == 1
    with pytest.raises(ValueError, match="SHA-256 摘要"):
        repo.plan_confirmation.confirm_creation_plan_from_reply(
            make_plan("bad-digest"),
            CreationTask(id="bad-task", workspace_id=WORKSPACE, plan_id="bad-digest",
                         name="bad", scenario="knowledge_explanation"),
            reply_reference={**REPLY_REFERENCE, "response_digest": "not-a-digest"},
            request_id="bad-request",
            verified_owner_user_id=OWNER,
        )


def test_transfer_requires_current_plan_topic_and_bound_completed_reply(tmp_path: Path) -> None:
    repo, plan, task = ready_task_for_transfer(tmp_path)
    prior_events = repo.execution_records.list_task_events(WORKSPACE, task.id)
    draft_task, run, script = transfer(repo, plan, task)
    assert draft_task.stage == "script" and draft_task.stage_status == "waiting_for_user"
    assert run.run_kind == "assistant_reply_transfer"
    assert run.source_reply_reference == REPLY_REFERENCE
    assert run.input_references == INPUT_REFERENCES
    assert script.source_reply_reference == REPLY_REFERENCE
    assert script.sections[0].content == REPLY_TEXT
    assert script.sections[0].evidence_reference_ids == []
    assert REPLY_TEXT not in str(repo.execution_records.list_task_events(WORKSPACE, task.id)[-1].payload)
    saved_events = repo.execution_records.list_task_events(WORKSPACE, task.id)
    assert saved_events[:-1] == prior_events
    assert saved_events[-1].event_type == "assistant_reply_script_transferred"

    retry_task, retry_run, retry_script = transfer(
        repo, plan, draft_task, script_id="fresh-unused-script", run_id="fresh-unused-run",
        expected_task_updated_at=draft_task.updated_at,
    )
    assert (retry_task.id, retry_run.id, retry_script.id) == (task.id, run.id, script.id)
    assert len(repo.script_records.list_creation_task_scripts(WORKSPACE, task.id)) == 1
    assert repo.execution_records.list_task_events(WORKSPACE, task.id) == saved_events

    with pytest.raises(ValueError, match="正文已变化"):
        transfer(repo, plan, task, reply_text="另一条回复")
    with pytest.raises(ValueError, match="必须保留当前对话回复正文"):
        transfer(repo, plan, task, request_id="partial-reply", sections=[
            replace(script.sections[0], content="只移交一部分")
        ])
    with pytest.raises(ValueError, match="固定版本不一致"):
        current_task = repo.task_records.get_creation_task(WORKSPACE, task.id)
        current_script = repo.script_records.list_creation_task_scripts(WORKSPACE, task.id)[0]
        transfer(
            repo, plan, current_task, request_id="changed-source-request",
            input_references=[{**INPUT_REFERENCES[0], "version_or_identity": "v2"}],
            replace_existing_script_id=current_script.id,
            expected_script_updated_at=current_script.updated_at,
        )
    assert len(repo.script_records.list_creation_task_scripts(WORKSPACE, task.id)) == 1


def test_concurrent_same_transfer_request_returns_one_persisted_draft(tmp_path: Path) -> None:
    repo, plan, task = ready_task_for_transfer(tmp_path)
    gate = threading.Barrier(2)

    def submit(index: int):
        gate.wait()
        return transfer(
            repo, plan, task,
            script_id=f"generated-script-{index}",
            run_id=f"generated-run-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results[0][1].id == results[1][1].id
    assert results[0][2].id == results[1][2].id
    assert len(repo.script_records.list_creation_task_scripts(WORKSPACE, task.id)) == 1
    transfer_events = [
        event for event in repo.execution_records.list_task_events(WORKSPACE, task.id)
        if event.event_type == "assistant_reply_script_transferred"
    ]
    assert len(transfer_events) == 1


def test_transfer_rejects_wrong_member_scope_and_stale_task_without_writes(tmp_path: Path) -> None:
    repo, plan, task = ready_task_for_transfer(tmp_path)
    prior_events = repo.execution_records.list_task_events(WORKSPACE, task.id)
    with pytest.raises(ValueError, match="没有绑定这条已完成对话回复"):
        transfer(repo, plan, task, verified_owner_user_id="other-member")
    with pytest.raises(ValueError, match="任务版本已变化"):
        transfer(repo, plan, task, expected_task_updated_at="2000-01-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="选题已变化"):
        transfer(repo, plan, task, expected_topic_id="another-topic")
    assert repo.script_records.list_creation_task_scripts(WORKSPACE, task.id) == []
    assert repo.execution_records.list_task_events(WORKSPACE, task.id) == prior_events


def test_existing_task_transfer_does_not_require_plan_created_from_reply(tmp_path: Path) -> None:
    repo, plan, task = ready_task_for_transfer(tmp_path, from_reply=False)
    _, run, script = transfer(
        repo, plan, task, plan_conversion_request_id=None,
    )
    assert run.source_reply_reference == REPLY_REFERENCE
    assert script.source_reply_reference == REPLY_REFERENCE
    assert len(repo.script_records.list_creation_task_scripts(WORKSPACE, task.id)) == 1


def test_reply_draft_replacement_and_manual_revision_preserve_saved_library_version(tmp_path: Path, monkeypatch) -> None:
    from test_production_foundation import edit_adoption_no_execution
    forbidden = edit_adoption_no_execution(monkeypatch)
    monkeypatch.setattr(main_module, "_creation_task_script_generator_for", forbidden)
    repo, plan, task = ready_task_for_transfer(tmp_path)
    _, _, original = transfer(repo, plan, task)
    current_task = repo.task_records.get_creation_task(WORKSPACE, task.id)
    with pytest.raises(ValueError, match="明确选择替换"):
        transfer(repo, plan, current_task, request_id="second-transfer")
    with pytest.raises(ValueError, match="草稿已变化"):
        transfer(
            repo, plan, current_task,
            request_id="stale-replacement",
            replace_existing_script_id=original.id,
            expected_script_updated_at="2000-01-01T00:00:00+00:00",
        )

    changed_section = replace(original.sections[0], content="用户修订的回复草稿")
    revised = repo.script_records.update_assistant_script_draft(
        WORKSPACE, task.id, original.id,
        title="用户已修订", sections=[changed_section],
        expected_updated_at=original.updated_at,
    )
    with pytest.raises(ValueError, match="请求身份已用于不同"):
        transfer(
            repo, plan, current_task,
            request_id="transfer-request-one",
            replace_existing_script_id=original.id,
            expected_script_updated_at=revised.updated_at,
        )
    replaced_task = repo.task_records.get_creation_task(WORKSPACE, task.id)
    _, _, replacement = transfer(
        repo, plan, replaced_task,
        request_id="explicit-replacement",
        replace_existing_script_id=original.id,
        expected_script_updated_at=revised.updated_at,
        script_id=original.id,
        run_id="reply-run-two",
    )
    assert replacement.id == original.id
    assert replacement.run_id == "reply-run-two"
    assert replacement.sections[0].content == REPLY_TEXT

    with pytest.raises(ValueError, match="画面建议和来源证据"):
        repo.script_records.confirm_creation_task_script(
            WORKSPACE, task.id, replacement.id,
            title="不能直接确认", sections=replacement.sections,
            expected_updated_at=replacement.updated_at,
        )
    with pytest.raises(ValueError, match="画面建议和来源证据"):
        repo.script_records.confirm_creation_task_script(
            WORKSPACE, task.id, replacement.id,
            title="缺画面建议", sections=[replace(
                replacement.sections[0],
                evidence_reference_ids=[INPUT_REFERENCES[0]["reference_id"]],
            )],
            expected_updated_at=replacement.updated_at,
        )
    with pytest.raises(ValueError, match="本次模型输入之外"):
        repo.script_records.update_assistant_script_draft(
            WORKSPACE, task.id, replacement.id,
            title="不能编造来源", sections=[replace(
                replacement.sections[0],
                evidence_reference_ids=["knowledge:other:v1"],
            )],
            expected_updated_at=replacement.updated_at,
        )
    completed_section = replace(
        replacement.sections[0],
        visual_prompt="展示当前资料中的核心功能。",
        evidence_reference_ids=[INPUT_REFERENCES[0]["reference_id"]],
    )
    replacement = repo.script_records.update_assistant_script_draft(
        WORKSPACE, task.id, replacement.id,
        title="补齐有效来源与画面建议", sections=[completed_section],
        expected_updated_at=replacement.updated_at,
    )

    before = replacement
    confirmed_task, confirmed = repo.script_records.confirm_creation_task_script(
        WORKSPACE, task.id, replacement.id,
        title="用户确认稿", sections=replacement.sections,
        expected_updated_at=replacement.updated_at,
    )
    assert confirmed.selection_status == "confirmed"
    assert confirmed_task.confirmed_script_id == replacement.id
    library_entry = repo.script_records.save_script_library_entry(
        WORKSPACE, source_script_id=replacement.id,
        name="明确保存的回复稿", category="品牌介绍",
    )
    read_back = repo.script_records.get_script_library_entry(WORKSPACE, library_entry.id)
    assert read_back.source_task_id == task.id
    assert read_back.source_script_id == replacement.id
    assert read_back.source_run_id == replacement.run_id
    assert read_back.sections[0].content == REPLY_TEXT
    with pytest.raises(ValueError, match="修订已变化"):
        repo.script_records.confirm_creation_task_script(
            WORKSPACE, task.id, replacement.id,
            title="不能覆盖的迟到回复", sections=[replace(before.sections[0], content="迟到覆盖")],
            expected_updated_at=before.updated_at,
        )
    assert repo.task_records.get_creation_task(WORKSPACE, task.id).confirmed_script_id == replacement.id
    assert repo.script_records.get_creation_task_script(WORKSPACE, replacement.id) == confirmed
    # A current CAS plus an explicit manual edit is a new revision. This
    # endpoint has no request identity that would make it a replay.
    manual_section = replace(confirmed.sections[0], content="用户明确手工修订后的正文")
    revised_task, revised_script = repo.script_records.confirm_creation_task_script(
        WORKSPACE, task.id, replacement.id,
        title="用户手工修订稿", sections=[manual_section],
        expected_updated_at=confirmed.updated_at,
        adoption_kind="manual", verified_owner_user_id=OWNER,
    )
    assert revised_task.confirmed_script_id == confirmed.id == revised_script.id
    assert revised_task.stage == "narration" and revised_task.stage_status == "not_started"
    assert revised_script.title == "用户手工修订稿"
    assert revised_script.sections == [manual_section]
    assert revised_script.selection_status == "confirmed"
    assert revised_script.updated_at != confirmed.updated_at
    assert revised_script.adoption["kind"] == "manual"
    assert revised_script.adoption["member_id"] == OWNER
    assert revised_script.adoption["version"] == revised_script.updated_at.isoformat()
    assert revised_script.adoption["body_digest"] == hashlib.sha256(manual_section.content.encode()).hexdigest()
    saved_library = repo.script_records.get_script_library_entry(WORKSPACE, library_entry.id)
    assert saved_library == read_back
    assert saved_library.sections[0].content == REPLY_TEXT != revised_script.sections[0].content
    with pytest.raises(ValueError, match="已确认文案不能"):
        transfer(
            repo,
            plan,
            revised_task,
            request_id="after-confirmation",
            replace_existing_script_id=replacement.id,
            expected_script_updated_at=revised_script.updated_at,
            script_id=replacement.id,
            run_id="reply-run-three",
        )
    assert repo.script_records.get_creation_task_script(WORKSPACE, replacement.id) == revised_script
    assert repo.script_records.get_script_library_entry(WORKSPACE, library_entry.id) == read_back


def test_http_transfer_reads_completed_reply_and_rechecks_current_source_then_replays_same_result(tmp_path, monkeypatch):
    repo, plan, task = ready_task_for_transfer(tmp_path)
    conversation_id = "11111111-1111-4111-8111-111111111111"
    turn_id = "22222222-2222-4222-8222-222222222222"
    turn = {
        "conversation_id": conversation_id,
        "status": "completed",
        "assistant_text": REPLY_TEXT,
        "result": {"response": {"reply": REPLY_TEXT}},
    }
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: WORKSPACE)
    monkeypatch.setattr(main_module, "_content_reader", lambda _request, _workspace: (OWNER, False))
    monkeypatch.setattr(main_module, "_validate_assistant_scope", lambda *_args: None)
    monkeypatch.setattr(main_module, "conversation_repo", SimpleNamespace(
        get=lambda _conversation_id, _workspace, _member: {
            "id": conversation_id, "scope_kind": "task", "scope_id": task.id,
        },
        get_turn=lambda _turn_id: turn,
    ))
    monkeypatch.setattr(main_module.ScriptContextUseCase, "read_inputs", lambda _case, _workspace, _task_id: (
        repo.task_records.get_creation_task(WORKSPACE, task.id), plan,
        SimpleNamespace(id=task.selected_topic_id, title="核心功能是什么"), (), INPUT_REFERENCES,
    ))
    readable = SimpleNamespace(
        version="v3", title="当前知识资料", use_state="available",
        authorization="available", ownership="workspace",
    )
    monkeypatch.setattr(main_module, "source_repository", SimpleNamespace(
        get_knowledge_reference_readable_by=lambda *_args, **_kwargs: readable,
    ))
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(CreationTaskTextResponseAdapter, "creation_task_scripts_response", lambda adapter, _workspace, _task: {"saved": True})
    request = Request({"type": "http", "headers": []})
    payload = AssistantReplyScriptTransferRequest(
        conversation_id=UUID(conversation_id), turn_id=UUID(turn_id),
        request_id=UUID("33333333-3333-4333-8333-333333333333"),
        expected_plan_updated_at=plan.updated_at,
        expected_task_updated_at=task.updated_at,
    )
    first = next(route.endpoint for route in main_module.app.routes if route.name == "transfer_assistant_reply_to_script")(task.id, request, payload)
    assert first == {"saved": True}
    saved = repo.script_records.list_creation_task_scripts(WORKSPACE, task.id)
    assert len(saved) == 1 and saved[0].source_reply_reference == {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "response_digest": hashlib.sha256(REPLY_TEXT.encode("utf-8")).hexdigest(),
    }
    assert saved[0].sections[0].content == REPLY_TEXT

    # The duplicate uses the original revision token; the repository returns the
    # original persisted script even though the first write advanced the task.
    second = next(route.endpoint for route in main_module.app.routes if route.name == "transfer_assistant_reply_to_script")(task.id, request, payload)
    assert second == {"saved": True}
    assert len(repo.script_records.list_creation_task_scripts(WORKSPACE, task.id)) == 1

    readable.version = "v2"
    changed_request = Request({"type": "http", "headers": []})
    with pytest.raises(main_module.HTTPException) as exc:
        next(route.endpoint for route in main_module.app.routes if route.name == "transfer_assistant_reply_to_script")(task.id, changed_request, payload)
    assert exc.value.status_code == 409


def test_http_plan_confirmation_trusts_home_reply_origin_and_replays_same_plan(tmp_path, monkeypatch):
    import json
    import httpx
    from test_creation_task_topic_provider import _review_tool_response
    from app.topics import CreationTaskTopicSource, OpenAICompatibleCreationTaskScriptGenerator

    repo = make_repo(tmp_path)
    conversation_id = "11111111-1111-4111-8111-111111111111"
    turn_id = "22222222-2222-4222-8222-222222222222"
    request_id = "44444444-4444-4444-8444-444444444444"
    turn = {
        "conversation_id": conversation_id,
        "status": "completed",
        "assistant_text": REPLY_TEXT,
        "result": {"response": {"reply": REPLY_TEXT}},
    }
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: WORKSPACE)
    monkeypatch.setattr(main_module, "_content_reader", lambda _request, _workspace: (OWNER, False))
    monkeypatch.setattr(main_module, "_validate_assistant_scope", lambda *_args: None)
    monkeypatch.setattr(main_module, "conversation_repo", SimpleNamespace(
        get=lambda _conversation_id, _workspace, _member: {
            "id": conversation_id, "scope_kind": "home", "scope_id": "home",
        },
        get_turn=lambda _turn_id: turn,
    ))
    monkeypatch.setattr(CreationPlanPreflightHttpAdapter, "preflight", lambda adapter, _request, _workspace, _payload: replace(
        make_plan(), preflight_fingerprint="f" * 64,
    ))
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(CreationTaskResponseAdapter, "creation_plan_response", lambda adapter, plan, task: {"plan_id": plan.id, "task_id": task.id})
    request = Request({"type": "http", "headers": []})
    payload = CreationPlanConfirmRequest(
        goal="介绍资料的核心功能", name="核心功能介绍", preflight_fingerprint="f" * 64,
        reply_origin={
            "conversation_id": conversation_id, "turn_id": turn_id, "request_id": request_id,
        },
    )

    first = next(route.endpoint for route in main_module.app.routes if route.name == "confirm_creation_plan")(request, payload)
    second = next(route.endpoint for route in main_module.app.routes if route.name == "confirm_creation_plan")(request, payload)
    assert second == first
    events = repo.execution_records.list_task_events(WORKSPACE, first["task_id"])
    assert len(events) == 1
    assert events[0].event_type == "assistant_reply_plan_confirmed"
    assert events[0].payload["reply_reference"] == {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "response_digest": hashlib.sha256(REPLY_TEXT.encode("utf-8")).hexdigest(),
    }
    assert REPLY_TEXT not in str(events[0].payload)

    plan = repo.plan_records.get_creation_plan(WORKSPACE, first["plan_id"])
    task = repo.task_records.get_creation_task(WORKSPACE, first["task_id"])
    topic_run = TopicGenerationRun(
        id="http-topic-run", workspace_id=WORKSPACE, task_id=task.id, plan_id=plan.id,
        status="running", route_snapshot={"provider": "test-double"},
        prompt_schema_version="topic-test/v1", input_references=INPUT_REFERENCES,
    )
    repo.topic_records.start_topic_generation(topic_run)
    topic = TopicCandidate(
        id="http-topic", workspace_id=WORKSPACE, task_id=task.id, run_id=topic_run.id,
        title="当前资料的核心功能", angle="讲清实际用途", audience="普通创作者",
        outline=["需求", "结果"], score=90,
    )
    repo.topic_records.complete_topic_generation(topic_run.id, [topic], [TopicEvidenceReference(
        id="http-topic-evidence", workspace_id=WORKSPACE, task_id=task.id,
        candidate_id=topic.id, kind="knowledge", reference_id="knowledge-one",
        version_or_identity="v3", title="当前知识资料", authorization_or_license="available",
        source_url=None,
    )])
    repo.topic_records.confirm_topic_candidate(WORKSPACE, task.id, topic.id)
    plan = repo.plan_records.get_creation_plan(WORKSPACE, plan.id)
    task = repo.task_records.get_creation_task(WORKSPACE, task.id)
    topic = repo.topic_records.list_topic_candidates(WORKSPACE, task.id)[0]
    readable = SimpleNamespace(
        version="v3", title="当前知识资料", use_state="available",
        authorization="available", ownership="workspace",
        body="资料介绍核心功能，示例文案可以简洁表达。",
    )
    bounded_source = CreationTaskTopicSource(reference_id="knowledge:knowledge-one:v3", kind="knowledge",
        title=readable.title, text=readable.body)
    review_requests = []
    def review(request):
        payload = json.loads(request.content)
        review_requests.append(payload)
        section = json.loads(payload["messages"][1]["content"])["script"]["sections"][0]
        response = {"sections": [{"index": 1,
            "supports": [{"candidate_quote_id": section["content"][0]["quote_id"],
                          "source_quote_id": json.loads(payload["messages"][1]["content"])["sources"][0]["text"][0]["quote_id"]}]}]}
        return _review_tool_response(response)
    checker = OpenAICompatibleCreationTaskScriptGenerator("https://models.invalid/v1", "controlled-review",
        transport=httpx.MockTransport(review))
    monkeypatch.setattr(main_module, "_creation_task_script_generator_for", lambda settings: checker)
    monkeypatch.setattr(main_module, "source_repository", SimpleNamespace(
        get_knowledge_reference_readable_by=lambda *_args, **_kwargs: readable,
    ))
    monkeypatch.setattr(main_module.ScriptContextUseCase, "read_inputs", lambda _case, _workspace, _task_id: (
        repo.task_records.get_creation_task(WORKSPACE, task.id), repo.plan_records.get_creation_plan(WORKSPACE, plan.id),
        topic, (bounded_source,), INPUT_REFERENCES,
    ))
    transfer_payload = AssistantReplyScriptTransferRequest(
        conversation_id=UUID(conversation_id), turn_id=UUID(turn_id),
        request_id=UUID("55555555-5555-4555-8555-555555555555"),
        plan_conversion_request_id=UUID(request_id),
        expected_plan_updated_at=plan.updated_at, expected_task_updated_at=task.updated_at,
    )
    transferred = next(route.endpoint for route in main_module.app.routes if route.name == "transfer_assistant_reply_to_script")(task.id, request, transfer_payload)
    assert transferred.task.id == task.id
    assert transferred.latest_run is None
    draft = repo.script_records.list_creation_task_scripts(WORKSPACE, task.id)[0]
    assert draft.selection_status == "draft"
    assert draft.sections[0].visual_prompt == "" and draft.sections[0].evidence_reference_ids == []
    assert draft.sections[0].content == REPLY_TEXT and draft.source_reply_reference is not None
    assert review_requests == []  # Original-text transfer is not a factual-review pass.

    edited = next(route.endpoint for route in main_module.app.routes if route.name == "save_assistant_script_draft")(task.id, request,
        main_module.CreationTaskScriptDraftRequest(
            edit_mode="replace_all", title=draft.title, expected_updated_at=draft.updated_at,
            sections=[{
                "id": draft.sections[0].id, "title": draft.sections[0].title,
                "content": draft.sections[0].content, "visual_prompt": "展示本次资料中的核心功能。",
                "evidence_reference_ids": ["knowledge:knowledge-one:v3"],
            }],
        ))
    assert edited.updated_at != draft.updated_at
    assert len(review_requests) == 1
    assert edited.source_reply_reference == draft.source_reply_reference
    assert edited.selection_status == "draft"
    confirmed = next(route.endpoint for route in main_module.app.routes if route.name == "confirm_creation_task_script")(task.id, draft.id, request,
        main_module.CreationTaskScriptConfirmRequest(
            title=edited.title, expected_updated_at=edited.updated_at,
            sections=[section.model_dump() for section in edited.sections],
        ))
    assert confirmed.task.confirmed_script_id == draft.id
    from app.schemas import ScriptLibrarySaveRequest
    entry = main_module._script_library_use_case(request).save_entry(WORKSPACE,
        ScriptLibrarySaveRequest(
            request_id=UUID("66666666-6666-4666-8666-666666666666"),
            expected_source_updated_at=repo.script_records.read_creation_task_script_header(WORKSPACE, draft.id)['updated_at'],
            source_script_id=draft.id, name="已确认的对话回复文案", category="产品介绍",
        ))
    read_back = repo.script_records.get_script_library_entry(WORKSPACE, entry['entry_id'])
    assert read_back.source_task_id == task.id and read_back.source_script_id == draft.id
    assert read_back.version == entry['version']

    prior_events = repo.execution_records.list_task_events(WORKSPACE, task.id)
    turn["result"] = {"response": {"reply": "被篡改的正文"}}
    with pytest.raises(main_module.HTTPException) as exc:
        next(route.endpoint for route in main_module.app.routes if route.name == "confirm_creation_plan")(request, payload)
    assert exc.value.status_code == 409
    assert repo.execution_records.list_task_events(WORKSPACE, first["task_id"]) == prior_events
