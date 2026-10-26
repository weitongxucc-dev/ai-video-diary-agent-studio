from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4
from types import SimpleNamespace
from fastapi.encoders import jsonable_encoder
from app.local_members import LocalMemberService
import json

from fastapi.testclient import TestClient
import pytest

import app.main as main
from app.production import (
    CreationPlan, CreationPlanKnowledgeReference, CreationTask, CreationTaskScript,
    CreationTaskScriptSection, ProductionRepository,
)
from app.source_repository import SourceRepository
from app.knowledge_files import parse_knowledge_file
from app.sources import KnowledgeItem, SourceConnector, SourceItem


@pytest.fixture
def library(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    repo = ProductionRepository(tmp_path / "studio.sqlite3", inbox)
    members=LocalMemberService(repo.database_path)
    owner,token=members.whoami(None)
    sources = SourceRepository(repo.database_path)
    knowledge = sources.create_knowledge_item_owned(KnowledgeItem(
        id="knowledge_own", workspace_id="local", content_type="text", title="本人产品资料",
        body="产品采用模块化结构。", file_reference=None, authorization="本人允许引用",
        ownership="本人", source_item_id=None, version="1",
    ),verified_user_id=owner.member_id)
    plan = CreationPlan(
        id="plan_original", workspace_id="local", goal="本人手写文案", name="原任务", target_count=1,
        language="zh-CN", aspect_ratio="9:16", duration_target_seconds=60,
        knowledge_references=[CreationPlanKnowledgeReference(
            id=knowledge.id, version=knowledge.version, title=knowledge.title,
            authorization=knowledge.authorization, ownership=knowledge.ownership,
        )], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary="未选材",
        model_snapshot=[], confirmation_points=[], blockers=[], preflight_fingerprint="manual-fixture",
    )
    task = CreationTask(
        id="task_original", workspace_id="local", plan_id=plan.id, name=plan.name,
        scenario="manual_fixture", stage="script", stage_status="waiting_for_user",
    )
    repo.confirm_creation_plan(plan, task,verified_owner_user_id=owner.member_id)
    # Existing manual draft fixture; no fake generation run or model result.
    script = CreationTaskScript(
        id="script_original", workspace_id="local", task_id=task.id, run_id="", topic_id="",
        title="模块化介绍", language="zh-CN", sections=[CreationTaskScriptSection(
            id="section_original", title="特点", content="模块化结构方便维护。", visual_prompt="产品特写",
            evidence_reference_ids=["knowledge:knowledge_own:1"],
        )],
    )
    with repo._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at) "
            "VALUES (?,?,?,?,?,?)", (script.id, "local", task.id, "", repo._script_payload(conn, script),
                                      script.updated_at.isoformat()),
        )
    repo.confirm_creation_task_script(
        "local", task.id, script.id, title=script.title, sections=script.sections,
        expected_updated_at=script.updated_at,
    )
    monkeypatch.setattr(main, "production_repo", repo)
    monkeypatch.setattr(main, "source_repository", sources)
    monkeypatch.setattr(main,"local_members_service",members)
    monkeypatch.setattr(main,"auth_context",SimpleNamespace(enabled=False))
    # Use the actual routes without starting background workers/models.
    client = TestClient(main.app,headers={"X-Local-Member-Token":token})
    yield client, repo, sources
    client.close()


def save(client,repo):
    response = client.post("/api/production/script-library", json={
        "request_id":str(uuid4()),"expected_source_updated_at":repo.script_records.read_creation_task_script_header('local','script_original')['updated_at'],
        "source_script_id": "script_original", "name": "维护介绍", "category": "产品",
    })
    assert response.status_code == 200, response.text
    return response.json()["entry"]


def reuse(client, entry_id, confirmed=True, *, expected_entry_updated_at=None):
    version=expected_entry_updated_at or client.get(f"/api/production/script-library/{entry_id}").json()["updated_at"]
    return client.post(f"/api/production/script-library/{entry_id}/reuse", json={
        "request_id":str(uuid4()),"expected_entry_updated_at":version,
        "name": "新介绍", "aspect_ratio": "16:9", "duration_target_seconds": 60,
        "requirements_confirmed": confirmed,
    })


def test_library_patch_without_configured_model_keeps_original_and_recoverable_refusal(library, monkeypatch):
    from app.config import Settings

    client, repo, _ = library
    created = reuse(client, save(client, repo)["id"]).json()
    task_id, script_id = created["task_id"], created["script_id"]
    before = repo.get_creation_task_script("local", script_id)
    unconfigured = Settings(llm_provider="minimax", llm_api_key=None, deepseek_api_key=None,
        llm_base_url="https://api.minimax.cn/v1", llm_model="review-model")
    assert not unconfigured.llm_configured
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: unconfigured)
    def no_provider_or_health_write(*args, **kwargs):
        raise AssertionError("unconfigured review must not construct a provider or record connection health")
    monkeypatch.setattr(main, "OpenAICompatibleCreationTaskScriptGenerator", no_provider_or_health_write)
    monkeypatch.setattr(main, "record_llm_service_verification", no_provider_or_health_write)
    section = asdict(before.sections[0])
    section["content"] += "请核对这一处修改。"
    request_id = str(uuid4())
    response = client.patch(f"/api/creation-tasks/{task_id}/scripts/{script_id}/sections", json={
        "request_id": request_id, "expected_updated_at": before.updated_at.isoformat(), "sections": [section],
    })
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"] == "route_configuration_invalid"
    assert "设置" in response.json()["detail"]["repair_action"]
    assert repo.get_creation_task_script("local", script_id) == before
    result = client.get(f"/api/creation-tasks/{task_id}/script-window-requests/{request_id}",
        params={"operation": "patch", "script_id": script_id, "include_input": "true"})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "failed"
    assert result.json()["submitted_input"]["sections"][0]["content"] == section["content"]


@pytest.mark.parametrize("provider", ["custom", "ollama"])
def test_script_generator_keeps_configured_keyless_routes(provider, monkeypatch):
    from app.config import Settings

    configured = Settings(llm_provider=provider, llm_base_url="http://127.0.0.1:9/v1",
        llm_model="local-review", llm_api_key=None, deepseek_api_key=None)
    calls = []
    marker = object()
    def construct(**kwargs):
        calls.append(kwargs)
        return marker
    monkeypatch.setattr(main, "OpenAICompatibleCreationTaskScriptGenerator", construct)
    assert main._creation_task_script_generator_for(configured) is marker
    assert calls == [{"base_url": "http://127.0.0.1:9/v1", "model": "local-review", "api_key": None}]


def test_save_versions_search_reuse_draft_and_new_confirmation_preserve_original(library):
    client, repo, _ = library
    with repo._connect() as conn:
        before_task = tuple(conn.execute("SELECT payload,updated_at FROM creation_tasks WHERE id='task_original'").fetchone())
        before_script = tuple(conn.execute("SELECT payload,updated_at FROM creation_task_scripts WHERE id='script_original'").fetchone())
    one, two = save(client,repo), save(client,repo)
    assert one["version"] == 1 and two["version"] == 2
    assert two["supersedes_entry_id"] == one["id"]
    page=client.get("/api/production/script-library?q=维护&category=产品").json()
    assert page["total_count"] is None and len(page["items"])==2 and page['next'] is None
    assert len(client.get("/api/production/script-library?q=模块化").json()["items"])==2
    assert client.get("/api/production/script-library?category=其他").json()["items"] == []
    assert reuse(client, one["id"], False).status_code == 409
    result = reuse(client, one["id"])
    assert result.status_code == 200, result.text
    body = result.json()
    draft=jsonable_encoder(asdict(repo.get_creation_task_script("local",body["script_id"])))
    task = repo.get_creation_task("local", body["task_id"])
    assert task.id != "task_original" and task.confirmed_script_id is None
    assert task.stage == "script" and task.stage_status == "waiting_for_user"
    assert task.current_timeline_version_id is None and task.narration_cache_key is None and task.montage_plan_id is None
    assert draft["selection_status"] == "draft" and draft["confirmed_at"] is None
    assert draft["run_id"] == "" and draft["topic_id"] == "" and draft["reused_from_entry_id"] == one["id"]
    assert draft["sections"][0]["id"] != "section_original"
    assert draft["sections"][0]["evidence_reference_ids"] == ["knowledge:knowledge_own:1"]
    assert client.get(f"/api/creation-tasks/{task.id}").status_code == 200
    stale_confirm = client.post(f"/api/creation-tasks/{task.id}/scripts/{draft['id']}/confirm", json={
        "title": draft["title"], "sections": draft["sections"],
        "expected_updated_at": "2000-01-01T00:00:00+00:00",
    })
    assert stale_confirm.status_code == 409
    assert repo.get_creation_task(task.workspace_id, task.id).confirmed_script_id is None
    confirm = client.post(f"/api/creation-tasks/{task.id}/scripts/{draft['id']}/confirm", json={
        "title": draft["title"], "sections": draft["sections"], "expected_updated_at": draft["updated_at"],
    })
    assert confirm.status_code == 200, confirm.text
    assert repo.get_creation_task("local", task.id).confirmed_script_id == draft["id"]
    with repo._connect() as conn:
        assert tuple(conn.execute("SELECT payload,updated_at FROM creation_tasks WHERE id='task_original'").fetchone()) == before_task
        assert tuple(conn.execute("SELECT payload,updated_at FROM creation_task_scripts WHERE id='script_original'").fetchone()) == before_script
        assert conn.execute("SELECT COUNT(*) FROM script_generation_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0] == 0
    assert repo.get_script_library_entry("local", one["id"]).version == 1


def test_live_source_version_or_permission_loss_refuses_read_reuse_and_confirm(library):
    client, repo, sources = library
    entry = save(client,repo)
    created = reuse(client, entry["id"]).json()
    with sources._connect() as conn:
        conn.execute("UPDATE knowledge_items SET version='2' WHERE id='knowledge_own'")
    states = client.get(f"/api/production/script-library/{entry['id']}/sources").json()
    assert states[0]["status"] == "version_changed" and states[0]["title"] is None
    assert client.get("/api/production/script-library").json()["items"][0]["id"] == entry["id"]
    assert reuse(client, entry["id"]).status_code == 409
    assert save(client,repo)["version"] == 2  # Owned confirmed history can still be saved.
    draft=jsonable_encoder(asdict(repo.get_creation_task_script("local",created["script_id"])))
    assert client.post(f"/api/creation-tasks/{created['task_id']}/scripts/{draft['id']}/confirm", json={
        "title": draft["title"], "sections": draft["sections"], "expected_updated_at": draft["updated_at"],
    }).status_code == 409
    assert repo.get_creation_task("local", created["task_id"]).confirmed_script_id is None
    with sources._connect() as conn:
        conn.execute("UPDATE knowledge_items SET workspace_id='other' WHERE id='knowledge_own'")
    states = client.get(f"/api/production/script-library/{entry['id']}/sources").json()
    assert states[0]["status"] == "missing" and states[0]["title"] is None


def test_reference_only_source_remains_a_reference_but_excluded_knowledge_is_blocked(library):
    client, repo, sources = library
    entry = save(client,repo)
    sources.set_knowledge_item_use_state("local", "knowledge_own", "reference_only")
    readable = client.get(f"/api/production/script-library/{entry['id']}/sources").json()
    assert readable[0]["status"] == "readable"
    sources.set_knowledge_item_use_state("local", "knowledge_own", "excluded")
    excluded = client.get(f"/api/production/script-library/{entry['id']}/sources").json()
    assert excluded[0]["status"] == "missing"
    assert excluded[0]["title"] is None
    assert reuse(client, entry["id"]).status_code == 409


def test_library_rechecks_reference_without_legacy_rights_proof_fields(library):
    client, repo, sources = library
    with sources._connect() as conn:
        conn.execute(
            "UPDATE knowledge_items SET authorization='', ownership='' WHERE id='knowledge_own'"
        )
    with repo._connect() as conn:
        row = conn.execute(
            "SELECT payload FROM creation_plans WHERE id='plan_original'"
        ).fetchone()
        payload = json.loads(row["payload"])
        payload["knowledge_references"][0]["authorization"] = ""
        payload["knowledge_references"][0]["ownership"] = ""
        conn.execute(
            "UPDATE creation_plans SET payload=? WHERE id='plan_original'",
            (json.dumps(payload),),
        )
    entry = save(client,repo)
    sources_state = client.get(
        f"/api/production/script-library/{entry['id']}/sources"
    ).json()
    assert sources_state[0]["status"] == "readable"
    assert reuse(client, entry["id"]).status_code == 200


def test_citation_reports_the_cited_version_facts_after_the_current_version_moved(library, monkeypatch):
    """引用回查必须给引用绑定那一版的真实快照事实，而不是当前版。"""
    client, repo, sources = library
    entry = save(client,repo)
    sources.upsert_connector(SourceConnector(
        id="c1", workspace_id="local", platform="knowledge_files", protocol="local_file",
        account_or_rule="知识文件导入", frequency="manual", permissions=(), cursor="",
        connection_status="ready", latest_run_id=None,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    ))
    # 用真实解析链造出有版本快照的资料：同文件更新内容后头推进到第2版，第1版仍在。
    parsed = parse_knowledge_file("manual.md", "# 参数\n\n## 规格\n\n- 净重: 600（g）\n".encode("utf-8"))
    first = SourceItem(
        id="source_versioned", workspace_id="local", connector_id="c1", source_label="file_import",
        source_url=None, content_type=parsed.content_type, author=None, published_at=None,
        body=parsed.full_text, subtitles=None, media_references=(), license="user_provided",
        title=parsed.title, file_reference=parsed.filename, content_hash=parsed.content_hash,
        sections=parsed.sections, parameters=parsed.parameters,
    )
    sources.save_source_items("local", [first])
    sources.save_source_item_to_knowledge("local", first.id, ownership="本人", authorization="本人允许引用",verified_owner_user_id=repo.get_creation_task("local","task_original").owner_user_id)
    with sources._connect() as conn:
        conn.execute("UPDATE knowledge_item_versions SET source_url='https://example.test/v1' "
                     "WHERE knowledge_item_id IN (SELECT id FROM knowledge_items WHERE file_reference='manual.md')")
    updated_body = "# 参数\n\n## 规格\n\n- 净重: 700（g）\n"
    updated = parse_knowledge_file("manual.md", updated_body.encode("utf-8"))
    second = SourceItem(
        id="source_versioned_2", workspace_id="local", connector_id="c1", source_label="file_import",
        source_url=None, content_type=updated.content_type, author=None, published_at=None,
        body=updated.full_text, subtitles=None, media_references=(), license="user_provided",
        title=updated.title, file_reference=updated.filename, content_hash=updated.content_hash,
        sections=updated.sections, parameters=updated.parameters,
    )
    sources.save_source_items("local", [second])
    versioned = sources.save_source_item_to_knowledge(
        "local", second.id, ownership="本人", authorization="本人允许引用",verified_owner_user_id=repo.get_creation_task("local","task_original").owner_user_id
    )
    assert versioned.version == "2"
    # 让已确认文案的引用指向这条真正有版本快照的资料，并同步方案的引用版本。
    with repo._connect() as conn:
        row = conn.execute("SELECT payload FROM creation_task_scripts WHERE id='script_original'").fetchone()
        script=repo.get_creation_task_script('local','script_original')
        script.sections[0].evidence_reference_ids=[f"knowledge:{versioned.id}:1"]
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE creation_task_scripts SET payload=? WHERE id='script_original'", (repo._script_payload(conn,script),))
        conn.execute(
            "UPDATE creation_plans SET payload=? WHERE id='plan_original'",
            (json.dumps({**json.loads(
                conn.execute("SELECT payload FROM creation_plans WHERE id='plan_original'").fetchone()[0]
            ), "knowledge_references": [asdict(CreationPlanKnowledgeReference(
                id=versioned.id, version="1", title=versioned.title,
                authorization=versioned.authorization, ownership=versioned.ownership,
            ))]}),),
        )
    repo.confirm_creation_task_script(
        "local", "task_original", "script_original", title="模块化介绍",
        sections=script.sections,
        expected_updated_at=repo.get_creation_task_script("local", "script_original").updated_at,
    )
    entry = save(client,repo)
    states = client.get(f"/api/production/script-library/{entry['id']}/sources").json()
    assert states[0]["reference_id"] == f"knowledge:{versioned.id}:1"
    # 引用绑定第1版，而当前头已是第2版：状态如实反映版本推进，回查事实仍是第1版。
    assert states[0]["status"] == "version_changed"
    assert states[0]["cited_version"] == "1"
    assert states[0]["cited_content_hash"] == parsed.content_hash
    assert states[0]["cited_source_url"] == "https://example.test/v1"
    # 引用只记到条目与版本，没有段落定位：不猜第一段。
    assert states[0]["locator"] is None
    # A shared task does not disclose a privately owned source's old snapshot.
    repo.set_creation_task_visibility("local", "task_original", "shared")
    sources.set_knowledge_item_visibility("local", versioned.id, "private")
    monkeypatch.setattr(main, "_content_reader", lambda *_: ("another-member", False))
    hidden = client.get(f"/api/production/script-library/{entry['id']}/sources")
    assert hidden.status_code == 200, hidden.text
    hidden_state = hidden.json()[0]
    assert hidden_state["status"] == "missing"
    assert all(hidden_state[field] is None for field in (
        "title", "source_url", "version_or_identity", "cited_version",
        "cited_source_url", "cited_fetched_at", "cited_content_hash", "locator"))


def test_cross_workspace_cannot_search_or_reuse(library, monkeypatch):
    client, repo, _ = library
    entry = save(client,repo)
    monkeypatch.setattr(main, "current_workspace_id", lambda request: "other")
    assert client.get("/api/production/script-library?q=维护").json()["items"] == []
    assert reuse(client, entry["id"],expected_entry_updated_at=entry["updated_at"]).status_code == 404
    assert client.get(f"/api/production/script-library/{entry['id']}/sources").status_code == 404


def test_owned_script_without_external_references_can_save_and_reuse(library):
    client, repo, _ = library
    with repo._connect() as conn:
        row = conn.execute("SELECT payload FROM creation_task_scripts WHERE id='script_original'").fetchone()
        script=repo.get_creation_task_script('local','script_original')
        script.sections[0].evidence_reference_ids=[]
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE creation_task_scripts SET payload=? WHERE id='script_original'", (repo._script_payload(conn,script),))
    entry = save(client,repo)
    assert client.get(f"/api/production/script-library/{entry['id']}/sources").json() == []
    response = reuse(client, entry["id"])
    assert response.status_code == 200, response.text
    assert repo.get_creation_task_script("local",response.json()["script_id"]).selection_status=="draft"


def test_library_use_case_reuse_rolls_back_receipt_failure_and_retries_original_request(library):
    """The business case can run directly; one transaction owns all reuse writes."""
    import sqlite3
    from app.creation.script_draft_use_case import ScriptDraftUseCase
    from app.creation.script_library_use_case import ScriptLibraryUseCase
    from app.creation.script_windows import ScriptWindowConflict
    from app.schemas import ScriptLibrarySaveRequest, ScriptLibraryReuseRequest

    _, repo, sources = library
    member_id = repo.get_creation_task('local', 'task_original').owner_user_id
    draft = ScriptDraftUseCase(repo, read_inputs=None, validate_inputs=None,
        script_goal=None, topic_origin=None, check_draft=None, record_service=None,
        route_snapshot=None, record_adoption=None)
    case = ScriptLibraryUseCase(repo, sources, reader=lambda _: (member_id, False), draft_use_case=draft)
    draft.read_script_header, draft.read_library_header = case.read_script_header, case.read_header
    header = case.read_script_header('local', 'script_original')
    saved = case.save_entry('local', ScriptLibrarySaveRequest(request_id=uuid4(),
        expected_source_updated_at=header['updated_at'], source_script_id=header['id'], name='独立用例'))
    payload = ScriptLibraryReuseRequest(request_id=uuid4(), name='原请求复用',
        expected_entry_updated_at=saved['entry']['updated_at'], requirements_confirmed=True)
    with pytest.raises(ScriptWindowConflict):
        case.reuse_entry('local', saved['entry_id'], payload.model_copy(update={
            'expected_entry_updated_at': datetime(2000, 1, 1, tzinfo=UTC)}))
    with repo._connect() as conn:
        conn.execute("CREATE TRIGGER fail_reuse_receipt BEFORE INSERT ON task_events "
            "WHEN NEW.event_type='script_library_reused' BEGIN SELECT RAISE(ABORT,'receipt failure'); END")
        tables = ('creation_tasks', 'creation_plans', 'creation_task_scripts',
                  'creation_task_script_sections', 'task_events')
        before = {table: conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in tables}
    with pytest.raises(ValueError) as failure:
        case.reuse_entry('local', saved['entry_id'], payload)
    assert isinstance(failure.value.__cause__, sqlite3.IntegrityError)
    assert str(failure.value.__cause__) == 'receipt failure'
    with repo._connect() as conn:
        assert {table: conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in tables} == before
        conn.execute('DROP TRIGGER fail_reuse_receipt')
    assert repo.script_records.library_reuse_request_result('local', str(payload.request_id), member_id) is None
    result = case.reuse_entry('local', saved['entry_id'], payload)
    assert case.reuse_entry('local', saved['entry_id'], payload) == result
    assert case.read_request('local', str(payload.request_id), 'reuse') == result
    assert repo.get_creation_task('local', result['task_id']).confirmed_script_id is None
