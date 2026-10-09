from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from app.production import (
    CreationPlan,
    CreationPlanKnowledgeReference,
    CreationTask,
    CreationTaskScriptSection,
    ProductionRepository,
    TopicCandidate,
    TopicEvidenceReference,
    TopicGenerationRun,
)

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
    request_id: str = "plan-request-one",
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
        return repo.confirm_creation_plan(plan, task, verified_owner_user_id=OWNER)
    return repo.confirm_creation_plan_from_reply(
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
    repo.start_topic_generation(run)
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
    repo.complete_topic_generation(run.id, [topic], [evidence])
    repo.confirm_topic_candidate(WORKSPACE, task.id, topic.id)
    plan = repo.get_creation_plan(WORKSPACE, plan.id)
    task = repo.get_creation_task(WORKSPACE, task.id)
    return repo, plan, task


def transfer(repo, plan, task, **overrides):
    values = {
        "workspace_id": WORKSPACE,
        "task_id": task.id,
        "request_id": "transfer-request-one",
        "reply_reference": REPLY_REFERENCE,
        "plan_conversion_request_id": "plan-request-one",
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
    return repo.transfer_completed_assistant_reply_to_script(**values)


def test_plan_confirmation_from_reply_is_atomic_idempotent_and_bound(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    plan, task = confirm_plan_from_reply(repo)
    retry_plan, retry_task = confirm_plan_from_reply(
        repo, plan_id="generated-new-plan-id", task_id="generated-new-task-id",
    )
    assert (retry_plan.id, retry_task.id) == (plan.id, task.id)
    event = repo.list_task_events(WORKSPACE, task.id)
    assert len(event) == 1
    assert event[0].event_type == "assistant_reply_plan_confirmed"
    assert event[0].payload["reply_reference"] == REPLY_REFERENCE
    assert REPLY_TEXT not in str(event[0].payload)

    with pytest.raises(ValueError, match="请求身份已用于不同"):
        repo.confirm_creation_plan_from_reply(
            make_plan("changed-plan"),
            CreationTask(
                id="changed-task", workspace_id=WORKSPACE, plan_id="changed-plan",
                name="不同目标", scenario="knowledge_explanation",
            ),
            reply_reference={**REPLY_REFERENCE, "turn_id": "another-turn"},
            request_id="plan-request-one",
            verified_owner_user_id=OWNER,
        )
    assert len(repo.list_task_events(WORKSPACE, task.id)) == 1
    with pytest.raises(ValueError, match="SHA-256 摘要"):
        repo.confirm_creation_plan_from_reply(
            make_plan("bad-digest"),
            CreationTask(id="bad-task", workspace_id=WORKSPACE, plan_id="bad-digest",
                         name="bad", scenario="knowledge_explanation"),
            reply_reference={**REPLY_REFERENCE, "response_digest": "not-a-digest"},
            request_id="bad-request",
            verified_owner_user_id=OWNER,
        )


def test_transfer_requires_current_plan_topic_and_bound_completed_reply(tmp_path: Path) -> None:
    repo, plan, task = ready_task_for_transfer(tmp_path)
    draft_task, run, script = transfer(repo, plan, task)
    assert draft_task.stage == "script" and draft_task.stage_status == "waiting_for_user"
    assert run.run_kind == "assistant_reply_transfer"
    assert run.source_reply_reference == REPLY_REFERENCE
    assert run.input_references == INPUT_REFERENCES
    assert script.source_reply_reference == REPLY_REFERENCE
    assert script.sections[0].content == REPLY_TEXT
    assert script.sections[0].evidence_reference_ids == []
    assert REPLY_TEXT not in str(repo.list_task_events(WORKSPACE, task.id)[-1].payload)

    retry_task, retry_run, retry_script = transfer(
        repo, plan, task, script_id="fresh-unused-script", run_id="fresh-unused-run",
    )
    assert (retry_task.id, retry_run.id, retry_script.id) == (task.id, run.id, script.id)
    assert len(repo.list_creation_task_scripts(WORKSPACE, task.id)) == 1
    assert len(repo.list_task_events(WORKSPACE, task.id)) == 2

    with pytest.raises(ValueError, match="正文已变化"):
        transfer(repo, plan, task, reply_text="另一条回复")
    with pytest.raises(ValueError, match="必须保留当前对话回复正文"):
        transfer(repo, plan, task, request_id="partial-reply", sections=[
            replace(script.sections[0], content="只移交一部分")
        ])
    with pytest.raises(ValueError, match="固定版本不一致"):
        current_task = repo.get_creation_task(WORKSPACE, task.id)
        current_script = repo.list_creation_task_scripts(WORKSPACE, task.id)[0]
        transfer(
            repo, plan, current_task, request_id="changed-source-request",
            input_references=[{**INPUT_REFERENCES[0], "version_or_identity": "v2"}],
            replace_existing_script_id=current_script.id,
            expected_script_updated_at=current_script.updated_at,
        )
    assert len(repo.list_creation_task_scripts(WORKSPACE, task.id)) == 1


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
    assert len(repo.list_creation_task_scripts(WORKSPACE, task.id)) == 1
    transfer_events = [
        event for event in repo.list_task_events(WORKSPACE, task.id)
        if event.event_type == "assistant_reply_script_transferred"
    ]
    assert len(transfer_events) == 1


def test_transfer_rejects_wrong_member_scope_and_stale_task_without_writes(tmp_path: Path) -> None:
    repo, plan, task = ready_task_for_transfer(tmp_path)
    with pytest.raises(ValueError, match="没有绑定这条已完成对话回复"):
        transfer(repo, plan, task, verified_owner_user_id="other-member")
    with pytest.raises(ValueError, match="任务版本已变化"):
        transfer(repo, plan, task, expected_task_updated_at="2000-01-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="选题已变化"):
        transfer(repo, plan, task, expected_topic_id="another-topic")
    assert repo.list_creation_task_scripts(WORKSPACE, task.id) == []
    assert len(repo.list_task_events(WORKSPACE, task.id)) == 1


def test_existing_task_transfer_does_not_require_plan_created_from_reply(tmp_path: Path) -> None:
    repo, plan, task = ready_task_for_transfer(tmp_path, from_reply=False)
    _, run, script = transfer(
        repo, plan, task, plan_conversion_request_id=None,
    )
    assert run.source_reply_reference == REPLY_REFERENCE
    assert script.source_reply_reference == REPLY_REFERENCE
    assert len(repo.list_creation_task_scripts(WORKSPACE, task.id)) == 1


def test_existing_draft_requires_explicit_current_revision_and_confirmed_script_is_immutable(tmp_path: Path) -> None:
    repo, plan, task = ready_task_for_transfer(tmp_path)
    _, _, original = transfer(repo, plan, task)
    current_task = repo.get_creation_task(WORKSPACE, task.id)
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
    revised = repo.update_assistant_script_draft(
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
    replaced_task = repo.get_creation_task(WORKSPACE, task.id)
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
        repo.confirm_creation_task_script(
            WORKSPACE, task.id, replacement.id,
            title="不能直接确认", sections=replacement.sections,
            expected_updated_at=replacement.updated_at,
        )
    with pytest.raises(ValueError, match="画面建议和来源证据"):
        repo.confirm_creation_task_script(
            WORKSPACE, task.id, replacement.id,
            title="缺画面建议", sections=[replace(
                replacement.sections[0],
                evidence_reference_ids=[INPUT_REFERENCES[0]["reference_id"]],
            )],
            expected_updated_at=replacement.updated_at,
        )
    with pytest.raises(ValueError, match="本次模型输入之外"):
        repo.update_assistant_script_draft(
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
    replacement = repo.update_assistant_script_draft(
        WORKSPACE, task.id, replacement.id,
        title="补齐有效来源与画面建议", sections=[completed_section],
        expected_updated_at=replacement.updated_at,
    )

    before = replacement
    confirmed_task, confirmed = repo.confirm_creation_task_script(
        WORKSPACE, task.id, replacement.id,
        title="用户确认稿", sections=replacement.sections,
        expected_updated_at=replacement.updated_at,
    )
    assert confirmed.selection_status == "confirmed"
    assert confirmed_task.confirmed_script_id == replacement.id
    library_entry = repo.save_script_library_entry(
        WORKSPACE, source_script_id=replacement.id,
        name="明确保存的回复稿", category="品牌介绍",
    )
    read_back = repo.get_script_library_entry(WORKSPACE, library_entry.id)
    assert read_back.source_task_id == task.id
    assert read_back.source_script_id == replacement.id
    assert read_back.source_run_id == replacement.run_id
    assert read_back.sections[0].content == REPLY_TEXT
    stale_retry_task, stale_retry = repo.confirm_creation_task_script(
        WORKSPACE, task.id, replacement.id,
        title="不能覆盖的迟到回复", sections=[replace(before.sections[0], content="迟到覆盖")],
        expected_updated_at=before.updated_at,
    )
    assert stale_retry_task.confirmed_script_id == replacement.id
    assert stale_retry.sections == before.sections
    with pytest.raises(ValueError, match="已确认文案不能"):
        transfer(
            repo,
            plan,
            confirmed_task,
            request_id="after-confirmation",
            replace_existing_script_id=replacement.id,
            expected_script_updated_at=confirmed.updated_at,
            script_id=replacement.id,
            run_id="reply-run-three",
        )
    assert repo.get_creation_task_script(WORKSPACE, replacement.id).sections == before.sections
