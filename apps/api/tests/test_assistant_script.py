from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main_module
from app.production import CreationPlan, CreationTask, ProductionRepository


def _script_task(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    repo = ProductionRepository(tmp_path / "isolated.sqlite3", library)
    plan = CreationPlan(
        id="plan-one", workspace_id="local", goal="讲清楚测试主题", name="测试视频",
        target_count=1, language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
        knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="测试", model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint="test",
    )
    task = CreationTask(
        id="task-one", workspace_id="local", plan_id=plan.id, name="测试视频",
        scenario="knowledge_explanation", stage="script", selected_topic_id="topic-one",
    )
    repo.confirm_creation_plan(plan, task)
    return repo


def test_script_turn_rejects_missing_authorized_sources_before_pi(tmp_path, monkeypatch):
    repo = _script_task(tmp_path)
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(repo, "list_topic_candidates", lambda _workspace_id, _task_id: [
        SimpleNamespace(id="topic-one", title="测试主题", angle="讲清楚", audience="普通人", outline=["开头"]),
    ])
    monkeypatch.setattr(main_module, "run_task_turn", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(AssertionError("Pi must not be called")))
    client = TestClient(main_module.app)
    for stage in ("topic", "narration", "montage", "review", "subtitle_cover", "script"):
        response = client.post("/api/creation-tasks/task-one/assistant/turn", json={
            "message": "请写开头", "context_stage": stage, "selected_object_refs": [],
        })
        assert response.status_code == 409, stage
        if stage == "script":
            assert response.json()["detail"]["reason"] == "content_sources_missing"
    foreign = client.post("/api/creation-tasks/foreign-task/assistant/turn", json={
        "message": "请写开头", "context_stage": "montage", "selected_object_refs": [],
    })
    assert foreign.status_code == 404
    assert repo.list_task_events("local", "task-one") == []


def test_script_turn_rejects_stale_sources_before_pi_or_events(tmp_path, monkeypatch):
    repo = _script_task(tmp_path)
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(repo, "list_topic_candidates", lambda _workspace_id, _task_id: [
        SimpleNamespace(id="topic-one"),
    ])
    def stale_sources(*_args, **_kwargs):
        raise main_module._CreationTaskTopicPreflightError(
            reason="knowledge_reference_stale", impact="来源版本已变化，未调用模型。",
            repair_action="重新确认来源。", retryable=True,
        )
    monkeypatch.setattr(main_module, "_creation_task_topic_inputs", stale_sources)
    monkeypatch.setattr(main_module, "run_task_turn", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(AssertionError("Pi must not be called")))
    response = TestClient(main_module.app).post("/api/creation-tasks/task-one/assistant/turn", json={
        "message": "请写开头", "context_stage": "script", "selected_object_refs": [],
    })
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "knowledge_reference_stale"
    assert repo.list_task_events("local", "task-one") == []


def test_review_task_allows_real_history_objects_and_rejects_foreign_refs(monkeypatch):
    events = []
    calls = []
    task = SimpleNamespace(id="task-one", stage="review", confirmed_script_id="script-one",
                           narration_cache_key="narration-one", current_timeline_version_id=None,
                           montage_plan_id="plan-one", visibility="private", owner_user_id=None)
    montage = SimpleNamespace(id="plan-one", task_id=task.id, script_id=task.confirmed_script_id,
                              status="rendered", output_path="rendered-video", duration_seconds=30,
                              clips=[SimpleNamespace(id="clip-current")], graphic_overlays=[])
    repo = SimpleNamespace(
        get_narration_artifact=lambda workspace_id, key: SimpleNamespace(
            task_id=task.id, script_id=task.confirmed_script_id,
        ),
        list_narration_segments=lambda workspace_id, task_id, script_id: [
            SimpleNamespace(id="segment-current", status="ready"),
        ],
        get_creation_task_montage_plan=lambda workspace_id, task_id: {},
        append_task_event=events.append,
    )
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "_creation_task_or_404", lambda workspace_id, task_id: task
                        if workspace_id == "local" and task_id == task.id else
                        (_ for _ in ()).throw(HTTPException(status_code=404, detail="task not found")))
    monkeypatch.setattr(main_module, "creation_task_montage_plan_from_payload", lambda payload: montage)
    monkeypatch.setattr(main_module, "_effective_audio_timeline", lambda plan: SimpleNamespace(tracks=[]))
    def run_turn(_settings, **kwargs):
        calls.append(kwargs)
        if "把真实排队状态显示给我" in kwargs["message"]:
            return "连续制作已提交并排队，尚未完成。", {
                "kind": "execution_action", "action": "start",
                "status": {"task_id": task.id, "job": {"id": "job-one", "status": "queued"},
                           "stages": [], "current_stage": "narration", "waiting_reason": None,
                           "status_updated_at": None, "video_ready": False,
                           "cover_status": "pending", "cover_message": "尚未生成封面。"},
            }
        return "请检查修改。", None
    monkeypatch.setattr(main_module, "run_task_turn", run_turn)
    client = TestClient(main_module.app)
    for stage, ref in (("montage", "clip:clip-current"), ("review", "narration:segment-current")):
        response = client.post("/api/creation-tasks/task-one/assistant/turn", json={
            "message": "请检查当前片段", "context_stage": stage, "selected_object_refs": [ref],
        })
        assert response.status_code == 200, response.text
    for ref in ("clip:clip-from-other-task", "clip:clip-removed"):
        response = client.post("/api/creation-tasks/task-one/assistant/turn", json={
            "message": "请检查旧镜头", "context_stage": "montage", "selected_object_refs": [ref],
        })
        assert response.status_code == 422
    foreign = client.post("/api/creation-tasks/foreign-task/assistant/turn", json={
        "message": "请检查当前片段", "context_stage": "montage", "selected_object_refs": [],
    })
    assert foreign.status_code == 404
    task.stage = "narration"
    premature = client.post("/api/creation-tasks/task-one/assistant/turn", json={
        "message": "请检查旧镜头", "context_stage": "montage", "selected_object_refs": [],
    })
    assert premature.status_code == 409
    started = client.post("/api/creation-tasks/task-one/assistant/turn", json={
        "message": "请现在按当前已确认文案制作视频，并把真实排队状态显示给我",
        "context_stage": "narration", "selected_object_refs": [],
    })
    assert started.status_code == 200, started.text
    assert started.json()["reply"] == "连续制作已提交并排队，尚未完成。"
    assert started.json()["edit_preview"] is None
    assert started.json()["execution_action"] == "start"
    assert started.json()["execution_status"]["job"]["id"] == "job-one"
    assert calls[-1]["message"] == "请现在按当前已确认文案制作视频，并把真实排队状态显示给我"
    assert "allowed_action" not in calls[-1]
    mixed = client.post("/api/creation-tasks/task-one/assistant/turn", json={
        "message": "开始制作，另外把字幕放大", "context_stage": "narration", "selected_object_refs": [],
    })
    assert mixed.status_code == 200, mixed.text
    assert "allowed_action" not in calls[-1]
    assert [(call["stage"], call["selected_refs"]) for call in calls] == [
        ("montage", ["clip:clip-current"]), ("review", ["narration:segment-current"]),
        ("narration", []), ("narration", []),
    ]
    assert len(events) == 8


def test_topic_scope_is_current_task_only():
    topic_task = SimpleNamespace(id="task-one", stage="topic")
    request = main_module.CreationTaskAssistantTurnRequest(
        message="请生成选题", context_stage="topic", selected_object_refs=[],
    )
    assert main_module._assistant_turn_scope("local", topic_task, request) == ("topic", [])


def test_script_draft_stays_unconfirmed_and_assistant_history_is_persisted(tmp_path, monkeypatch):
    repo = _script_task(tmp_path)
    topic = SimpleNamespace(id="topic-one", title="测试主题", angle="讲清楚", audience="普通人", outline=["开头"])
    source = SimpleNamespace(reference_id="knowledge:one:v1", title="已授权资料", text="可核实的文字摘录")

    def inputs(workspace_id, task_id):
        task = main_module._creation_task_or_404(workspace_id, task_id)
        if task.stage != "script" or task.confirmed_script_id:
            raise HTTPException(status_code=409, detail="当前任务不在待确认文案阶段")
        return task, repo.get_creation_plan(workspace_id, task.plan_id), topic, (source,), [
            {"reference_id": source.reference_id, "kind": "knowledge", "object_id": "one"},
        ]

    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "_assistant_script_inputs", inputs)
    client = TestClient(main_module.app)
    base = "/api/creation-tasks/task-one/assistant"
    context = client.get(base + "/script-context")
    assert context.status_code == 200
    assert context.json()["sources"][0]["reference_id"] == source.reference_id
    body = {"title": "待确认文案", "sections": [{
        "id": "section-one", "title": "开头", "content": "根据已授权资料，讲清楚测试主题。",
        "visual_prompt": "展示真实资料", "evidence_reference_ids": [source.reference_id],
    }]}
    invented = {**body, "sections": [{**body["sections"][0], "evidence_reference_ids": ["made-up"]}]}
    assert client.post(base + "/script-draft", json=invented).status_code == 422
    assert repo.list_creation_task_scripts("local", "task-one") == []

    first = client.post(base + "/script-draft", json=body)
    assert first.status_code == 200
    script_id = first.json()["id"]
    assert first.json()["selection_status"] == "draft"
    assert repo.get_creation_task("local", "task-one").confirmed_script_id is None
    second = client.post(base + "/script-draft", json={
        **body, "sections": [{**body["sections"][0], "content": "把开头缩短。"}],
    })
    assert second.status_code == 200 and second.json()["id"] == script_id
    assert len(repo.list_creation_task_scripts("local", "task-one")) == 1

    monkeypatch.setattr(main_module, "run_task_turn", lambda _settings, **_kwargs:
                        ("草稿已更新，请检查后确认。", {"script_id": script_id}))
    turn = client.post(base + "/turn", json={
        "message": "把开头缩短", "context_stage": "script", "selected_object_refs": [],
    })
    assert turn.status_code == 200
    assert turn.json()["script_draft"]["id"] == script_id
    assert turn.json()["edit_preview"] is None
    persisted = ProductionRepository(tmp_path / "isolated.sqlite3", tmp_path / "library")
    events = persisted.list_task_events("local", "task-one")
    assert [(item.payload["role"], item.payload["stage"]) for item in events] == [
        ("user", "script"), ("assistant", "script"),
    ]
    history = client.get(base + "/history")
    assert history.status_code == 200
    assert [item["message"] for item in history.json()["items"]] == [
        "把开头缩短", "草稿已更新，请检查后确认。",
    ]
    assert "private-test-key" not in history.text
    rejected_secret = client.post(base + "/turn", json={
        "message": "api_key=private-test-key 请加到文案", "context_stage": "script",
        "selected_object_refs": [],
    })
    assert rejected_secret.status_code == 422
    assert len(repo.list_task_events("local", "task-one")) == 2

    saved = repo.get_creation_task_script("local", script_id)
    repo.confirm_creation_task_script("local", "task-one", script_id,
                                      title=saved.title, sections=saved.sections)
    assert client.post(base + "/script-draft", json=body).status_code == 409
    for stage in ("script", "topic", "narration", "montage", "review", "subtitle_cover"):
        assert client.post(base + "/turn", json={
            "message": "继续修改", "context_stage": stage, "selected_object_refs": [],
        }).status_code == 409, stage
    assert len(repo.list_task_events("local", "task-one")) == 2
    assert repo.get_creation_task("local", "task-one").confirmed_script_id == script_id
