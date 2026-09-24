from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.creation_execution import CreationExecution, revision_digest
from app.production import CreationPlan, CreationTask, ProductionRepository


def _seed(tmp_path: Path, *, workspace_id: str = "workspace_a", confirmed: bool = True):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    repo = ProductionRepository(database_path=tmp_path / "isolated.sqlite3", video_library_path=library)
    task_id = f"task_{workspace_id}"
    plan = CreationPlan(
        id=f"plan_{workspace_id}", workspace_id=workspace_id,
        goal="一条测试视频", name="测试", target_count=1, language="zh-CN",
        aspect_ratio="9:16", duration_target_seconds=30,
        knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="测试", model_snapshot=[], confirmation_points=[],
        blockers=[], preflight_fingerprint="test",
    )
    task = CreationTask(id=task_id, workspace_id=workspace_id, plan_id=plan.id,
                        name="测试", scenario="knowledge_explanation")
    repo.confirm_creation_plan(plan, task)
    now = datetime.now(timezone.utc).isoformat()
    script_id = f"script_{workspace_id}"
    payload = {
        "id": script_id, "workspace_id": workspace_id, "task_id": task_id,
        "run_id": f"run_{workspace_id}", "topic_id": f"topic_{workspace_id}",
        "title": "已确认文案", "language": "zh-CN",
        "sections": [{"id": "section_1", "title": "正文", "content": "真实待制作文案",
                      "visual_prompt": "现场", "evidence_reference_ids": ["source_1"]}],
        "selection_status": "confirmed" if confirmed else "draft",
        "created_at": now, "updated_at": now,
        "confirmed_at": now if confirmed else None,
    }
    with sqlite3.connect(repo.database_path) as conn:
        conn.execute(
            "INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at) VALUES (?,?,?,?,?,?)",
            (script_id, workspace_id, task_id, payload["run_id"], json.dumps(payload), now),
        )
        if confirmed:
            task.confirmed_script_id = script_id
            task.stage = "narration"
            conn.execute(
                "UPDATE creation_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=?",
                (json.dumps(asdict(task), default=str), now, workspace_id, task_id),
            )
    script = repo.get_creation_task_script(workspace_id, script_id)
    persisted_plan = repo.get_creation_plan(workspace_id, plan.id)
    return repo, task_id, script_id, revision_digest(script, persisted_plan)


def _execution(repo, actions=None, render_ready=lambda _w, _t: True):
    actions = actions or {}
    return CreationExecution(
        repo,
        run_narration=actions.get("narration", lambda _w, _t: None),
        run_montage_plan=actions.get("montage_plan", lambda _w, _t: None),
        run_render=actions.get("render", lambda _w, _t: None),
        render_is_valid=render_ready,
    )


def test_draft_and_wrong_workspace_cannot_start(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path, confirmed=False)
    execution = _execution(repo)
    with pytest.raises(ValueError, match="确认当前文案"):
        execution.start("workspace_a", task_id, script_id, digest)
    assert not repo.list_job_runs("workspace_a", task_id)
    with pytest.raises(KeyError):
        execution.start("workspace_b", task_id, script_id, digest)


def test_duplicate_start_and_restart_execute_one_persisted_job(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path)
    monkeypatch.setattr(repo, "get_creation_task_montage_plan", lambda *_: {})
    calls = []
    execution = _execution(repo, {
        "narration": lambda *_: calls.append("narration"),
        "montage_plan": lambda *_: calls.append("montage_plan"),
        "render": lambda *_: calls.append("render"),
    })
    first = execution.start("workspace_a", task_id, script_id, digest)
    second = execution.start("workspace_a", task_id, script_id, digest)
    assert first.id == second.id
    assert len(execution.jobs("workspace_a", task_id)) == 1
    restarted = _execution(repo, execution.actions)
    assert restarted.execute("workspace_a", first.id).status == "succeeded"
    assert restarted.execute("workspace_a", first.id).status == "succeeded"
    assert calls == ["narration", "montage_plan", "render"]


def test_failure_resume_reuses_valid_narration(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path)
    audio = tmp_path / "narration.wav"
    attempts = []
    monkeypatch.setattr(repo, "get_creation_task_montage_plan", lambda *_: {})
    monkeypatch.setattr(repo, "get_narration_artifact", lambda *_: SimpleNamespace(
        task_id=task_id, script_id=script_id, audio_path=str(audio)))

    def narration(workspace_id, current_task_id):
        attempts.append("narration")
        audio.write_bytes(b"synthetic audio")
        task = repo.get_creation_task(workspace_id, current_task_id)
        task.narration_cache_key = "synthetic_cache"
        with sqlite3.connect(repo.database_path) as conn:
            conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                         (json.dumps(asdict(task), default=str), workspace_id, current_task_id))

    def montage(*_):
        attempts.append("montage_plan")
        if attempts.count("montage_plan") == 1:
            raise ValueError("素材暂不可用")

    execution = _execution(repo, {"narration": narration, "montage_plan": montage,
                                  "render": lambda *_: attempts.append("render")})
    first = execution.start("workspace_a", task_id, script_id, digest)
    assert execution.execute("workspace_a", first.id).status == "failed"
    assert "素材暂不可用" in execution.latest("workspace_a", task_id).failure_reason
    resumed = execution.retry("workspace_a", task_id)
    assert resumed.id != first.id
    assert execution.execute("workspace_a", resumed.id).status == "succeeded"
    assert attempts == ["narration", "montage_plan", "montage_plan", "render"]


def test_cancel_at_boundary_and_revision_change_blocks_resume(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path)
    monkeypatch.setattr(repo, "get_creation_task_montage_plan", lambda *_: {})
    calls = []
    execution = _execution(repo, {"narration": lambda *_: calls.append("narration")})
    first = execution.start("workspace_a", task_id, script_id, digest)
    assert execution.cancel("workspace_a", task_id).status == "cancelled"
    assert execution.execute("workspace_a", first.id).status == "cancelled"
    assert calls == []
    with sqlite3.connect(repo.database_path) as conn:
        row = conn.execute("SELECT payload FROM creation_task_scripts WHERE id=?", (script_id,)).fetchone()
        payload = json.loads(row[0])
        payload["sections"][0]["content"] = "修改后的文案"
        conn.execute("UPDATE creation_task_scripts SET payload=? WHERE id=?", (json.dumps(payload), script_id))
    with pytest.raises(ValueError, match="变化"):
        execution.retry("workspace_a", task_id)


def test_http_start_requires_explicit_confirmation_and_is_idempotent(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path, workspace_id="local")
    scheduled = []
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "creation_execution", _execution(repo))
    monkeypatch.setattr(main_module, "execution_worker", SimpleNamespace(
        submit=lambda *args: scheduled.append(args)))
    client = TestClient(main_module.app)
    base = f"/api/creation-tasks/{task_id}/execution"
    preview = client.get(base + "/preview")
    assert preview.status_code == 200
    assert preview.json()["input_digest"] == digest
    rejected = client.post(base + "/start", json={
        "script_id": script_id, "input_digest": digest, "requirements_confirmed": False,
    })
    assert rejected.status_code == 409
    assert not repo.list_job_runs("local", task_id)
    payload = {"script_id": script_id, "input_digest": digest,
               "requirements_confirmed": True}
    first = client.post(base + "/start", json=payload)
    second = client.post(base + "/start", json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    assert len(repo.list_job_runs("local", task_id)) == 1
    assert len(scheduled) == 2  # atomic JobRun claim ensures only one worker executes
