from dataclasses import replace

import pytest
from fastapi import HTTPException

import app.main as main
from app.production import (
    CreationPlan, CreationPlanKnowledgeReference, CreationPlanSourceReference, CreationTask,
    CreationTaskScript, CreationTaskScriptSection, ProductionRepository, ScriptGenerationRun,
    TopicCandidate, TopicEvidenceReference, TopicGenerationRun,
)
from app.source_repository import SourceRepository
from app.sources import KnowledgeItem, SourceConnector, SourceItem


@pytest.fixture
def evidence_context(tmp_path, monkeypatch):
    sources = SourceRepository(tmp_path / "sources.sqlite3")
    production = ProductionRepository(tmp_path / "production.sqlite3", tmp_path / "library")
    monkeypatch.setattr(main, "source_repository", sources)
    monkeypatch.setattr(main, "production_repo", production)
    knowledge = sources.create_knowledge_item_owned(KnowledgeItem(
        id="knowledge", workspace_id="qa", content_type="text", title="真实资料",
        body="第一版事实正文。", file_reference=None, authorization="", ownership="",
        source_item_id=None, version="1", use_state="available",
    ), verified_user_id="owner")
    # The fixed historical version remains readable; its provenance is truly empty.
    sources.edit_knowledge_item("qa", knowledge.id, expected_version="1", title=knowledge.title,
                                body="第二版新事实。", verified_user_id="owner")
    sources.upsert_connector(SourceConnector(id="manual", workspace_id="qa", platform="手工来源",
        protocol="manual", account_or_rule="manual", frequency="manual", permissions=()))
    source = SourceItem(
        id="manual-source", workspace_id="qa", connector_id="manual", source_label="手工来源", source_url=None,
        content_type="text", author=None, published_at=None, body="手工来源事实。", subtitles=None,
        media_references=(), license="unknown", title="手工来源", use_state="available",
    )
    sources.save_source_items("qa", [source])
    plan = CreationPlan(
        id="plan", workspace_id="qa", goal="介绍资料", name="真实追溯", target_count=1,
        language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
        knowledge_references=[CreationPlanKnowledgeReference(knowledge.id, "1", knowledge.title, "", "")],
        source_references=[CreationPlanSourceReference(source.id, source.connector_id, "手工来源",
                           source.title, source.license, main.source_item_identity(source))],
        selected_voice=None, pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="无媒体", model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint="fixture",
    )
    task = CreationTask(id="task", workspace_id="qa", plan_id=plan.id, name=plan.name,
                        scenario="knowledge_explanation")
    plan, task = production.confirm_creation_plan(plan, task, verified_owner_user_id="owner")
    return sources, production, plan, task


def start_topic(context):
    _, production, plan, task = context
    texts, inputs, evidence = main._creation_task_topic_inputs("qa", task, plan, cloud_egress=True)
    assert texts[0].text == "第一版事实正文。"
    run = TopicGenerationRun(
        id="topic-run", workspace_id="qa", task_id=task.id, plan_id=plan.id,
        status="running", route_snapshot={"provider": "controlled"},
        prompt_schema_version="fixture", input_references=inputs,
    )
    production.start_topic_generation(run)
    candidate = TopicCandidate(
        id="candidate", workspace_id="qa", task_id=task.id, run_id=run.id,
        title="介绍事实", angle="说明事实", audience="创作者", outline=["事实"], score=90,
    )
    references = [TopicEvidenceReference(
        id=f"evidence-{number}", workspace_id="qa", task_id=task.id, candidate_id=candidate.id,
        **{key: value for key, value in item.items()
           if key in {"kind", "reference_id", "version_or_identity", "title", "authorization_or_license", "source_url"}},
    ) for number, item in enumerate(evidence.values())]
    return run, candidate, references


def test_empty_knowledge_provenance_survives_topic_and_script_persistence(evidence_context):
    sources, production, plan, task = evidence_context
    run, candidate, references = start_topic(evidence_context)
    assert references[0].authorization_or_license == ""
    _, completed, _, persisted = production.complete_topic_generation(run.id, [candidate], references)
    assert completed.status == "succeeded"
    assert [(item.version_or_identity, item.authorization_or_license) for item in persisted] == [
        ("1", ""), (plan.source_references[0].identity, "unknown"),
    ]
    production.confirm_topic_candidate("qa", task.id, candidate.id)
    sources.set_knowledge_item_use_state("qa", "knowledge", "excluded")
    with pytest.raises(HTTPException) as blocked:
        main._assistant_script_inputs("qa", task.id)
    assert blocked.value.status_code == 409 and blocked.value.detail["reason"] == "knowledge_reference_missing"
    sources.set_knowledge_item_use_state("qa", "knowledge", "available")
    task, _, _, _, inputs = main._assistant_script_inputs("qa", task.id)
    script_run = ScriptGenerationRun(
        id="script-run", workspace_id="qa", task_id=task.id, plan_id=plan.id, topic_id=candidate.id,
        status="running", route_snapshot={}, prompt_schema_version="fixture", input_references=inputs,
    )
    production.start_script_generation(script_run)
    script = CreationTaskScript(
        id="script", workspace_id="qa", task_id=task.id, run_id=script_run.id, topic_id=candidate.id,
        title="真实事实", language="zh-CN", sections=[CreationTaskScriptSection(
            id="section", title="事实", content="第一版事实正文。", visual_prompt="展示资料",
            evidence_reference_ids=[item["reference_id"] for item in inputs],
        )],
    )
    with pytest.raises(ValueError, match="本次模型输入之外"):
        production.complete_script_generation(script_run.id, replace(script, sections=[
            replace(script.sections[0], evidence_reference_ids=["knowledge:knowledge:2"]),
        ]))
    _, finished, saved = production.complete_script_generation(script_run.id, script)
    assert finished.status == "succeeded" and saved.sections[0].evidence_reference_ids == [
        "knowledge:knowledge:1", "source:manual-source",
    ]
    # Reference reading remains available after confirmation, independently of script editing gates.
    task, saved = production.confirm_creation_task_script('qa',task.id,saved.id,
        title=saved.title,sections=saved.sections,expected_updated_at=saved.updated_at,verified_owner_user_id='owner')
    from app.creation.segment_production_use_case import SegmentProductionUseCase
    from app.creation_execution import CreationExecutionActor
    case = SegmentProductionUseCase(production,narration_stage=None,renderer=None,
        artifact_store=None,output_root='unused',source_repository=sources)
    actor = CreationExecutionActor('qa','owner')
    view = case.sources('qa',task.id,'section',actor,offset=1,limit=1,max_chars=4)
    assert view['items'][0]['binding']['version_or_identity']==inputs[1]['version_or_identity']
    assert view['items'][0]['status']=='matched' and view['items'][0]['text']=='手工来源'
    assert view['items'][0]['truncated'] and view['total_count']==2
    sources.save_source_items('qa',[replace(sources.get_source_item('qa','manual-source'),body='同ID后来替换的正文')])
    changed = case.sources('qa',task.id,'section',actor,offset=1,limit=1)
    assert changed['items'][0]['status']=='changed' and 'text' not in changed['items'][0]
    unknown = case.source_bindings(replace(saved,run_id='missing-historical-run'),saved.sections[0])
    assert all(item['binding_status']=='unknown' and item['version_or_identity'] is None for item in unknown)


@pytest.mark.parametrize("restriction", ["excluded", "not_for_automation", "wrong-version", "wrong-workspace", "wrong-owner"])
def test_empty_provenance_does_not_bypass_reference_preflight(evidence_context, restriction):
    sources, _, plan, task = evidence_context
    if restriction in {"excluded", "not_for_automation"}:
        sources.set_knowledge_item_use_state("qa", "knowledge", restriction)
    elif restriction == "wrong-version":
        plan = replace(plan, knowledge_references=[replace(plan.knowledge_references[0], version="missing")])
    elif restriction == "wrong-workspace":
        plan = replace(plan, workspace_id="another")
    else:
        task = replace(task, owner_user_id="another-member")
    with pytest.raises(main._CreationTaskTopicPreflightError):
        main._creation_task_topic_inputs("qa", task, plan, cloud_egress=True)


@pytest.mark.parametrize("change", [
    {"workspace_id": "another"}, {"task_id": "another"}, {"reference_id": ""},
    {"version_or_identity": ""}, {"candidate_id": "unknown"},
])
def test_empty_provenance_keeps_evidence_identity_and_association_checks(evidence_context, change):
    _, production, _, _ = evidence_context
    run, candidate, references = start_topic(evidence_context)
    with pytest.raises(ValueError, match="选题证据"):
        production.complete_topic_generation(run.id, [candidate], [replace(references[0], **change), references[1]])
    assert production.list_topic_candidates("qa", "task") == []


def test_empty_provenance_keeps_topic_score_hard_limit(evidence_context):
    _, production, _, _ = evidence_context
    run, candidate, references = start_topic(evidence_context)
    with pytest.raises(ValueError, match="选题候选"):
        production.complete_topic_generation(run.id, [replace(candidate, score=101)], references)
    assert production.list_topic_candidates("qa", "task") == []
