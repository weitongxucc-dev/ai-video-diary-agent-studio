from dataclasses import asdict
from datetime import UTC, datetime
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
    sources = SourceRepository(repo.database_path)
    knowledge = sources.create_knowledge_item(KnowledgeItem(
        id="knowledge_own", workspace_id="local", content_type="text", title="本人产品资料",
        body="产品采用模块化结构。", file_reference=None, authorization="本人允许引用",
        ownership="本人", source_item_id=None, version="1",
    ))
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
    repo.confirm_creation_plan(plan, task)
    # Existing manual draft fixture; no fake generation run or model result.
    script = CreationTaskScript(
        id="script_original", workspace_id="local", task_id=task.id, run_id="", topic_id="",
        title="模块化介绍", language="zh-CN", sections=[CreationTaskScriptSection(
            id="section_original", title="特点", content="模块化结构方便维护。", visual_prompt="产品特写",
            evidence_reference_ids=["knowledge:knowledge_own:1"],
        )],
    )
    with repo._connect() as conn:
        conn.execute(
            "INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at) "
            "VALUES (?,?,?,?,?,?)", (script.id, "local", task.id, "", json.dumps(asdict(script), default=str),
                                      script.updated_at.isoformat()),
        )
    repo.confirm_creation_task_script("local", task.id, script.id, title=script.title, sections=script.sections)
    monkeypatch.setattr(main, "production_repo", repo)
    monkeypatch.setattr(main, "source_repository", sources)
    # Use the actual routes without starting background workers/models.
    client = TestClient(main.app)
    yield client, repo, sources
    client.close()


def save(client):
    response = client.post("/api/production/script-library", json={
        "source_script_id": "script_original", "name": "维护介绍", "category": "产品",
    })
    assert response.status_code == 200, response.text
    return response.json()


def reuse(client, entry_id, confirmed=True):
    return client.post(f"/api/production/script-library/{entry_id}/reuse", json={
        "name": "新介绍", "aspect_ratio": "16:9", "duration_target_seconds": 60,
        "requirements_confirmed": confirmed,
    })


def test_save_versions_search_reuse_draft_and_new_confirmation_preserve_original(library):
    client, repo, _ = library
    with repo._connect() as conn:
        before_task = tuple(conn.execute("SELECT payload,updated_at FROM creation_tasks WHERE id='task_original'").fetchone())
        before_script = tuple(conn.execute("SELECT payload,updated_at FROM creation_task_scripts WHERE id='script_original'").fetchone())
    one, two = save(client), save(client)
    assert one["version"] == 1 and two["version"] == 2
    assert two["supersedes_entry_id"] == one["id"]
    assert client.get("/api/production/script-library?q=维护&category=产品").json()["total_count"] == 2
    assert client.get("/api/production/script-library?q=模块化").json()["total_count"] == 2
    assert client.get("/api/production/script-library?category=其他").json()["items"] == []
    assert reuse(client, one["id"], False).status_code == 409
    result = reuse(client, one["id"])
    assert result.status_code == 200, result.text
    body = result.json()
    draft = body["script"]
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
    entry = save(client)
    created = reuse(client, entry["id"]).json()
    with sources._connect() as conn:
        conn.execute("UPDATE knowledge_items SET version='2' WHERE id='knowledge_own'")
    states = client.get(f"/api/production/script-library/{entry['id']}/sources").json()
    assert states[0]["status"] == "version_changed" and states[0]["title"] is None
    assert client.get("/api/production/script-library").json()["items"][0]["id"] == entry["id"]
    assert reuse(client, entry["id"]).status_code == 409
    assert save(client)["version"] == 2  # Owned confirmed history can still be saved.
    draft = created["script"]
    assert client.post(f"/api/creation-tasks/{created['task_id']}/scripts/{draft['id']}/confirm", json={
        "title": draft["title"], "sections": draft["sections"], "expected_updated_at": draft["updated_at"],
    }).status_code == 409
    assert repo.get_creation_task("local", created["task_id"]).confirmed_script_id is None
    with sources._connect() as conn:
        conn.execute("UPDATE knowledge_items SET workspace_id='other' WHERE id='knowledge_own'")
    states = client.get(f"/api/production/script-library/{entry['id']}/sources").json()
    assert states[0]["status"] == "missing" and states[0]["title"] is None


def test_reference_only_source_remains_a_reference_but_excluded_knowledge_is_blocked(library):
    client, _, sources = library
    entry = save(client)
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
    entry = save(client)
    sources_state = client.get(
        f"/api/production/script-library/{entry['id']}/sources"
    ).json()
    assert sources_state[0]["status"] == "readable"
    assert reuse(client, entry["id"]).status_code == 200


def test_citation_reports_the_cited_version_facts_after_the_current_version_moved(library):
    """引用回查必须给引用绑定那一版的真实快照事实，而不是当前版。"""
    client, repo, sources = library
    entry = save(client)
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
    sources.save_source_item_to_knowledge("local", first.id, ownership="本人", authorization="本人允许引用")
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
        "local", second.id, ownership="本人", authorization="本人允许引用"
    )
    assert versioned.version == "2"
    # 让已确认文案的引用指向这条真正有版本快照的资料，并同步方案的引用版本。
    with repo._connect() as conn:
        row = conn.execute("SELECT payload FROM creation_task_scripts WHERE id='script_original'").fetchone()
        payload = json.loads(row[0])
        payload["sections"][0]["evidence_reference_ids"] = [f"knowledge:{versioned.id}:1"]
        conn.execute("UPDATE creation_task_scripts SET payload=? WHERE id='script_original'", (json.dumps(payload),))
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
        sections=[CreationTaskScriptSection(**section) for section in payload["sections"]],
    )
    entry = save(client)
    states = client.get(f"/api/production/script-library/{entry['id']}/sources").json()
    assert states[0]["reference_id"] == f"knowledge:{versioned.id}:1"
    # 引用绑定第1版，而当前头已是第2版：状态如实反映版本推进，回查事实仍是第1版。
    assert states[0]["status"] == "version_changed"
    assert states[0]["cited_version"] == "1"
    assert states[0]["cited_content_hash"] == parsed.content_hash
    assert states[0]["cited_source_url"] == "https://example.test/v1"
    # 引用只记到条目与版本，没有段落定位：不猜第一段。
    assert states[0]["locator"] is None


def test_cross_workspace_cannot_search_or_reuse(library, monkeypatch):
    client, _, _ = library
    entry = save(client)
    monkeypatch.setattr(main, "current_workspace_id", lambda request: "other")
    assert client.get("/api/production/script-library?q=维护").json()["items"] == []
    assert reuse(client, entry["id"]).status_code == 404
    assert client.get(f"/api/production/script-library/{entry['id']}/sources").status_code == 404


def test_owned_script_without_external_references_can_save_and_reuse(library):
    client, repo, _ = library
    with repo._connect() as conn:
        row = conn.execute("SELECT payload FROM creation_task_scripts WHERE id='script_original'").fetchone()
        payload = json.loads(row[0])
        payload["sections"][0]["evidence_reference_ids"] = []
        conn.execute("UPDATE creation_task_scripts SET payload=? WHERE id='script_original'", (json.dumps(payload),))
    entry = save(client)
    assert client.get(f"/api/production/script-library/{entry['id']}/sources").json() == []
    response = reuse(client, entry["id"])
    assert response.status_code == 200, response.text
    assert response.json()["script"]["selection_status"] == "draft"
