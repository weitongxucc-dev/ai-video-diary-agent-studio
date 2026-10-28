from __future__ import annotations

from pathlib import Path

import pytest

from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan, CreationPlanKnowledgeReference
from app.creation.topic_contracts import (
    TopicCandidate,
    TopicEvidenceReference,
    TopicGenerationRun,
)
from app.creation.contracts import CreationTask
from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection, ScriptGenerationRun


WORKSPACE = "workspace-cas"
OWNER = "member-cas"
REFERENCE_ID = "knowledge:source-one:v1"


def ready_script(tmp_path: Path) -> tuple[ProductionRepository, CreationTaskScript]:
    library = tmp_path / "library"
    library.mkdir()
    repo = ProductionRepository(tmp_path / "production.sqlite", library)
    plan = CreationPlan(
        id="plan-cas",
        workspace_id=WORKSPACE,
        goal="介绍资料内容",
        name="资料介绍",
        target_count=1,
        language="zh-CN",
        aspect_ratio="9:16",
        duration_target_seconds=30,
        knowledge_references=[CreationPlanKnowledgeReference(
            id="source-one", version="v1", title="资料", authorization="available", ownership="workspace",
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
        preflight_fingerprint="cas-test",
    )
    task = CreationTask(
        id="task-cas", workspace_id=WORKSPACE, plan_id=plan.id, name=plan.name,
        scenario="knowledge_explanation", stage="topic",
    )
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id=OWNER)
    topic_run = TopicGenerationRun(
        id="topic-run-cas", workspace_id=WORKSPACE, task_id=task.id, plan_id=plan.id,
        status="running", route_snapshot={"provider": "test-double"},
        prompt_schema_version="topic-test/v1",
        input_references=[{"reference_id": REFERENCE_ID, "kind": "knowledge", "object_id": "source-one",
                           "version_or_identity": "v1", "title": "资料"}],
    )
    repo.topic_records.start_topic_generation(topic_run)
    topic = TopicCandidate(
        id="topic-cas", workspace_id=WORKSPACE, task_id=task.id, run_id=topic_run.id,
        title="资料介绍", angle="说明核心内容", audience="创作者", outline=["内容"], score=90,
    )
    evidence = TopicEvidenceReference(
        id="topic-evidence-cas", workspace_id=WORKSPACE, task_id=task.id,
        candidate_id=topic.id, kind="knowledge", reference_id="source-one",
        version_or_identity="v1", title="资料", authorization_or_license="available", source_url=None,
    )
    repo.topic_records.complete_topic_generation(topic_run.id, [topic], [evidence])
    repo.topic_records.confirm_topic_candidate(WORKSPACE, task.id, topic.id)
    script_run = ScriptGenerationRun(
        id="script-run-cas", workspace_id=WORKSPACE, task_id=task.id, plan_id=plan.id,
        topic_id=topic.id, status="running", route_snapshot={"provider": "test-double"},
        prompt_schema_version="script-test/v1",
        input_references=[{"reference_id": REFERENCE_ID}],
    )
    repo.script_records.start_script_generation(script_run)
    section = CreationTaskScriptSection(
        id="section-cas", title="核心内容", content="资料说明正文。", visual_prompt="展示资料中的核心内容。",
        evidence_reference_ids=[REFERENCE_ID],
    )
    _, _, script = repo.script_records.complete_script_generation(
        script_run.id,
        CreationTaskScript(
            id="script-cas", workspace_id=WORKSPACE, task_id=task.id, run_id=script_run.id,
            topic_id=topic.id, title="资料介绍", language="zh-CN", sections=[section],
        ),
    )
    return repo, script


def update(repo: ProductionRepository, script: CreationTaskScript, token: str | None):
    return repo.script_records.update_assistant_script_draft(
        WORKSPACE, script.task_id, script.id, title="更新后", sections=script.sections,
        expected_updated_at=token,
    )


def confirm(repo: ProductionRepository, script: CreationTaskScript, token: str | None):
    return repo.script_records.confirm_creation_task_script(
        WORKSPACE, script.task_id, script.id, title="确认稿", sections=script.sections,
        expected_updated_at=token,
    )


def test_existing_script_update_requires_current_revision_without_writing(tmp_path: Path) -> None:
    repo, script = ready_script(tmp_path)
    original_task = repo.task_records.get_creation_task(WORKSPACE, script.task_id)

    for token, message in ((None, "必须提供当前 updated_at"), ("2000-01-01T00:00:00+00:00", "修订已变化")):
        with pytest.raises(ValueError, match=message):
            update(repo, script, token)
        assert repo.script_records.get_creation_task_script(WORKSPACE, script.id) == script
        assert repo.task_records.get_creation_task(WORKSPACE, script.task_id) == original_task

    revised = update(repo, script, script.updated_at.isoformat())
    assert revised.title == "更新后"
    assert revised.updated_at != script.updated_at
    assert repo.script_records.get_creation_task_script(WORKSPACE, script.id) == revised
    with pytest.raises(ValueError, match="修订已变化"):
        update(repo, script, script.updated_at.isoformat())
    assert repo.script_records.get_creation_task_script(WORKSPACE, script.id) == revised


def test_confirmation_requires_current_revision_before_any_confirmation_write(tmp_path: Path) -> None:
    repo, script = ready_script(tmp_path)
    original_task = repo.task_records.get_creation_task(WORKSPACE, script.task_id)

    for token, message in ((None, "必须提供当前 updated_at"), ("2000-01-01T00:00:00+00:00", "修订已变化")):
        with pytest.raises(ValueError, match=message):
            confirm(repo, script, token)
        assert repo.script_records.get_creation_task_script(WORKSPACE, script.id) == script
        assert repo.task_records.get_creation_task(WORKSPACE, script.task_id) == original_task

    task, confirmed = confirm(repo, script, script.updated_at.isoformat())
    assert confirmed.selection_status == "confirmed"
    assert task.confirmed_script_id == script.id
    assert task.stage == "narration"
    confirmed_task = repo.task_records.get_creation_task(WORKSPACE, script.task_id)
    for token, message in ((None, "必须提供当前 updated_at"),
                           (script.updated_at.isoformat(), "修订已变化")):
        with pytest.raises(ValueError, match=message):
            confirm(repo, script, token)
        assert repo.script_records.get_creation_task_script(WORKSPACE, script.id) == confirmed
        assert repo.task_records.get_creation_task(WORKSPACE, script.task_id) == confirmed_task


def test_confirmation_cas_does_not_bypass_visual_or_evidence_requirements(tmp_path: Path) -> None:
    repo, script = ready_script(tmp_path)
    incomplete = [CreationTaskScriptSection(
        id=script.sections[0].id, title=script.sections[0].title, content=script.sections[0].content,
        visual_prompt="", evidence_reference_ids=[],
    )]

    with pytest.raises(ValueError, match="画面建议和来源证据"):
        repo.script_records.confirm_creation_task_script(
            WORKSPACE, script.task_id, script.id, title="不允许确认", sections=incomplete,
            expected_updated_at=script.updated_at,
        )
    assert repo.script_records.get_creation_task_script(WORKSPACE, script.id) == script
    assert repo.task_records.get_creation_task(WORKSPACE, script.task_id).confirmed_script_id is None
