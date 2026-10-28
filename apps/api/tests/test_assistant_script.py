from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main_module
from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan, CreationPlanKnowledgeReference
from app.creation.contracts import CreationTask
from app.conversation_repository import ConversationRepository
from app.local_members import LocalMemberService


def _assistant_task_session(tmp_path: Path, monkeypatch, task_id: str):
    conversations = ConversationRepository(tmp_path / "assistant-conversations.sqlite3")
    members = LocalMemberService(tmp_path / "assistant-members.sqlite3")
    _member, token = members.join(members.create_invite_code(), "成员甲")
    member_id = members.resolve_member(token).member_id
    monkeypatch.setattr(main_module, "conversation_repo", conversations)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "local")
    conversation = conversations.current_or_create("local", member_id, "task", task_id)
    return conversations, member_id, token, conversation


def _task_turn_payload(conversations, member_id: str, conversation: dict, message: str,
                       stage: str, refs: list[str] | None = None, *, playhead_ms=None, reference_version=None):
    current = conversations.get(conversation["id"], conversation["workspace_id"], member_id)
    saved = conversations.save_draft(
        conversation["id"], conversation["workspace_id"], member_id,
        message, current["draft_version"],
    )
    conversation.update(draft_text=saved["draft_text"], draft_version=saved["draft_version"])
    return {
        "conversation_id": conversation["id"], "message": message,
        "client_request_id": str(uuid4()), "expected_draft_version": saved["draft_version"],
        "context_stage": stage, "selected_object_refs": refs or [], "playhead_ms": playhead_ms,
        **({"expected_reference_version": reference_version} if reference_version is not None else {}),
    }


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
        scenario="knowledge_explanation", stage="script", selected_topic_id="topic-one", visibility="shared",
    )
    repo.plan_confirmation.confirm_creation_plan(plan, task)
    return repo


def test_script_turn_rejects_missing_authorized_sources_before_pi(tmp_path, monkeypatch):
    repo = _script_task(tmp_path)
    conversations, member_id, token, conversation = _assistant_task_session(tmp_path, monkeypatch, "task-one")
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(repo.topic_records, "list_topic_candidates", lambda _workspace_id, _task_id: [
        SimpleNamespace(id="topic-one", title="测试主题", angle="讲清楚", audience="普通人", outline=["开头"]),
    ])
    monkeypatch.setattr(main_module, "run_task_turn", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(AssertionError("Pi must not be called")))
    client = TestClient(main_module.app)
    for stage in ("topic", "narration", "montage", "review", "subtitle_cover", "script"):
        response = client.post("/api/creation-tasks/task-one/assistant/turn", headers={"X-Local-Member-Token": token},
                               json=_task_turn_payload(conversations, member_id, conversation, "请写开头", stage))
        assert response.status_code == 409, stage
        if stage == "script":
            assert response.json()["detail"]["reason"] == "content_sources_missing"
    foreign = client.post("/api/creation-tasks/foreign-task/assistant/turn", headers={"X-Local-Member-Token": token},
                          json=_task_turn_payload(conversations, member_id, conversation, "请写开头", "montage"))
    assert foreign.status_code == 404
    assert repo.execution_records.list_task_events("local", "task-one") == []


def test_script_turn_rejects_stale_sources_before_pi_or_events(tmp_path, monkeypatch):
    repo = _script_task(tmp_path)
    conversations, member_id, token, conversation = _assistant_task_session(tmp_path, monkeypatch, "task-one")
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(repo.topic_records, "list_topic_candidates", lambda _workspace_id, _task_id: [
        SimpleNamespace(id="topic-one"),
    ])
    def stale_sources(*_args, **_kwargs):
        raise main_module._CreationTaskTopicPreflightError(
            reason="knowledge_reference_stale", impact="来源版本已变化，未调用模型。",
            repair_action="重新确认来源。", retryable=True,
        )
    monkeypatch.setattr(main_module.CreationSourceInputs, "prepare", stale_sources)
    monkeypatch.setattr(main_module, "run_task_turn", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(AssertionError("Pi must not be called")))
    response = TestClient(main_module.app).post(
        "/api/creation-tasks/task-one/assistant/turn", headers={"X-Local-Member-Token": token},
        json=_task_turn_payload(conversations, member_id, conversation, "请写开头", "script"),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "knowledge_reference_stale"
    assert repo.execution_records.list_task_events("local", "task-one") == []


def test_review_task_allows_real_history_objects_and_rejects_foreign_refs(tmp_path, monkeypatch):
    from dataclasses import asdict
    from datetime import datetime
    import json
    from starlette.requests import Request
    from app.creation.contracts import TimelineVersion
    from app.creation_execution import CreationExecution, CreationExecutionActor
    from app.montage import MontageClip
    from app.creation.contracts import JobRun, TaskEvent
    from app.production import MaterialAsset, MaterialSegment

    conversations, member_id, token, conversation = _assistant_task_session(tmp_path, monkeypatch, "task-one")
    material_repo = _script_task(tmp_path)
    path = material_repo.video_library_path / "source.mp4"
    path.write_bytes(b"isolated-selection-metadata")
    material = material_repo._save_asset(MaterialAsset(
        id="asset-current", workspace_id="local", file_name=path.name, path=str(path),
        size_bytes=path.stat().st_size, modified_at=datetime.fromtimestamp(path.stat().st_mtime),
        owner_user_id=member_id, visibility="private", duration_seconds=3,
        segments=[MaterialSegment(id="material-segment-current", asset_id="asset-current",
            start_seconds=0, end_seconds=3)],
    ))
    clip = MontageClip(id="clip-current", cue_id="cue-current", asset_id=material.id,
        segment_id=material.segments[0].id, source_path="workspace://source.mp4",
        source_start_seconds=0, source_end_seconds=3, timeline_start_seconds=0,
        timeline_end_seconds=3, match_score=1, match_reasons=["existing selected material"],
        needs_confirmation=False)
    calls = []
    task = CreationTask(id="task-one", workspace_id="local", plan_id="plan-one", name="选择范围验收",
                           scenario="选择范围验收", stage="review", confirmed_script_id="script-one",
                           narration_cache_key="narration-one", current_timeline_version_id="scope-timeline",
                           montage_plan_id="plan-one", visibility="shared", owner_user_id=None)
    montage = SimpleNamespace(id="plan-one", task_id=task.id, script_id=task.confirmed_script_id,
                              status="rendered", output_path="rendered-video", duration_seconds=30,
                              clips=[clip], graphic_overlays=[])
    with material_repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id=?", (json.dumps(asdict(task), default=str), task.id))
    material_repo.timeline_records.create_timeline_version(TimelineVersion(
        id=task.current_timeline_version_id, workspace_id=task.workspace_id, task_id=task.id,
        parent_version_id=None, sequence=1, created_by_operation_id=None,
        snapshot={"montage": {"clips": [asdict(clip)]}, "narration_segments": [
            {"id": "segment-current", "workspace_id": task.workspace_id, "task_id": task.id}]}))
    task = material_repo.task_records.get_creation_task("local", task.id)
    monkeypatch.setattr(material_repo, "get_narration_artifact", lambda workspace_id, key: SimpleNamespace(
            task_id=task.id, script_id=task.confirmed_script_id,
        ))
    monkeypatch.setattr(material_repo.narration_records, "list_narration_segments", lambda workspace_id, task_id, script_id: [
            SimpleNamespace(id="segment-current", status="ready"),
        ])
    monkeypatch.setattr(material_repo, "get_creation_task_montage_plan", lambda workspace_id, task_id: {})
    monkeypatch.setattr(main_module, "production_repo", material_repo)
    monkeypatch.setattr(main_module, "creation_task_montage_plan_from_payload", lambda payload: montage)
    monkeypatch.setattr(main_module, "_effective_audio_timeline", lambda plan: SimpleNamespace(tracks=[]))
    def no_execution(*_):
        pytest.fail("this scope test must not run a media executor")
    monkeypatch.setattr(main_module, "creation_execution", CreationExecution(material_repo,
        run_narration=no_execution, run_montage_plan=no_execution, run_render=no_execution,
        render_is_valid=lambda *_: False))
    def run_turn(_settings, **kwargs):
        calls.append(kwargs)
        if "把真实排队状态显示给我" in kwargs["message"]:
            bound_request = Request({"type": "http", "headers": [(b"x-local-member-token", token.encode())]})
            source, existing = main_module._assistant_execution_source(task.id, bound_request, kwargs["turn_id"],
                "start", "actual-scope-input")
            assert existing is None
            job = material_repo.job_records.create_job_run(JobRun(id="job-one", workspace_id="local", task_id=task.id,
                kind="creation_task_execution", idempotency_key="scope-execution", input_digest="actual-scope-input"))
            actor = CreationExecutionActor("local", member_id)
            material_repo.execution_records.append_task_event(TaskEvent(id=f"event_{job.id}_start", workspace_id="local",
                task_id=task.id, job_id=job.id, event_type="creation_execution_started",
                payload={"input_digest": job.input_digest, **actor.identity_payload()}))
            status = main_module._bind_assistant_execution_result(task.id, bound_request, kwargs["turn_id"],
                main_module._creation_execution_status("local", task.id), source=source)
            return "连续制作已提交并排队，尚未完成。", {
                "kind": "execution_action", "action": "start",
                "status": status,
            }
        return "请检查修改。", None
    monkeypatch.setattr(main_module, "run_task_turn", run_turn)
    client = TestClient(main_module.app)
    headers = {"X-Local-Member-Token": token}
    for stage, ref in (("montage", "clip:clip-current"), ("review", "narration:segment-current")):
        current = conversations.get(conversation["id"], "local", member_id)
        selected = conversations.save_reference(conversation["id"], "local", member_id,
            {"kind": "selection", "items": [{"kind": "shot" if ref.startswith("clip:") else "narration",
                "task_id": task.id, "object_id": ref.split(":", 1)[1], "version": task.current_timeline_version_id,
                "label": "当前镜头" if ref.startswith("clip:") else "当前配音"}]}, current["reference_version"])
        if stage == "montage":
            stale = client.post("/api/creation-tasks/task-one/assistant/turn", headers=headers, json=
                _task_turn_payload(conversations, member_id, conversation, "请检查当前片段", stage, [ref],
                    reference_version=selected["reference_version"] - 1))
            assert stale.status_code == 409 and "选中引用已变化" in stale.text
            assert calls == []
        response = client.post("/api/creation-tasks/task-one/assistant/turn", headers=headers, json=
                               _task_turn_payload(conversations, member_id, conversation, "请检查当前片段", stage, [ref],
                                   reference_version=selected["reference_version"]))
        assert response.status_code == 200, response.text
    current = conversations.get(conversation["id"], "local", member_id)
    conversations.save_reference(conversation["id"], "local", member_id, None, current["reference_version"])
    for ref in ("clip:clip-from-other-task", "clip:clip-removed"):
        response = client.post("/api/creation-tasks/task-one/assistant/turn", headers=headers, json=
                               _task_turn_payload(conversations, member_id, conversation, "请检查旧镜头", "montage", [ref]))
        assert response.status_code == 422
    foreign = client.post("/api/creation-tasks/foreign-task/assistant/turn", headers=headers, json=
                          _task_turn_payload(conversations, member_id, conversation, "请检查当前片段", "montage"))
    assert foreign.status_code == 404
    task.stage = "narration"
    with material_repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id=?", (json.dumps(asdict(task), default=str), task.id))
    premature = client.post("/api/creation-tasks/task-one/assistant/turn", headers=headers, json=
                            _task_turn_payload(conversations, member_id, conversation, "请检查旧镜头", "montage"))
    assert premature.status_code == 409
    started_message = "请现在按当前已确认文案制作视频，并把真实排队状态显示给我"
    started = client.post("/api/creation-tasks/task-one/assistant/turn", headers=headers, json=
                          _task_turn_payload(conversations, member_id, conversation, started_message, "narration"))
    assert started.status_code == 200, started.text
    assert started.json()["reply"] == "连续制作已提交并排队，尚未完成。"
    assert started.json()["edit_preview"] is None
    assert started.json()["execution_action"] == "start"
    assert started.json()["execution_status"]["job"]["id"] == "job-one"
    assert calls[-1]["message"] == started_message
    assert "allowed_action" not in calls[-1]
    mixed = client.post("/api/creation-tasks/task-one/assistant/turn", headers=headers, json=
                        _task_turn_payload(conversations, member_id, conversation, "开始制作，另外把字幕放大", "narration"))
    assert mixed.status_code == 200, mixed.text
    assert "allowed_action" not in calls[-1]
    assert [(call["stage"], call["selected_refs"]) for call in calls] == [
        ("montage", ["clip:clip-current"]), ("review", ["narration:segment-current"]),
        ("narration", []), ("narration", []),
    ]
    selection = calls[0]["material_selection"]
    assert selection["asset_id"] == material.id and selection["segment_id"] == material.segments[0].id
    assert selection["expected_updated_at"] == material.updated_at.isoformat()
    assert selection["expected_segment_updated_at"] == material.segments[0].updated_at.isoformat()
    assert {item.event_type for item in material_repo.execution_records.list_task_events("local", task.id)} == {
        "creation_execution_started", "assistant_execution_located"}  # Actual binding facts; no legacy chat rows.
    saved_turns = conversations.list_turns(conversation["id"])
    assert [(item["user_message"], item["status"]) for item in saved_turns] == [
        ("请检查当前片段", "completed"), ("请检查当前片段", "completed"),
        ("请现在按当前已确认文案制作视频，并把真实排队状态显示给我", "completed"),
        ("开始制作，另外把字幕放大", "completed"),
    ]


def test_topic_scope_is_current_task_only(monkeypatch):
    from test_production_foundation import edit_adoption_no_execution
    forbidden = edit_adoption_no_execution(monkeypatch)
    monkeypatch.setattr(main_module, "production_repo", SimpleNamespace())
    monkeypatch.setattr(main_module, "_script_context_use_case", forbidden)
    monkeypatch.setattr(main_module, "_metadata_edit_use_case", forbidden)
    topic_task = SimpleNamespace(id="task-one", stage="topic")
    request = main_module.CreationTaskAssistantTurnRequest(
        conversation_id=uuid4(), message="请生成选题", client_request_id=uuid4(), expected_draft_version=0,
        context_stage="topic", selected_object_refs=[],
    )
    assert main_module._assistant_turn_scope("local", topic_task, request) == ("topic", [])


def test_confirmed_task_without_production_artifacts_can_use_general_assistant_scope(monkeypatch):
    from test_production_foundation import edit_adoption_no_execution
    forbidden = edit_adoption_no_execution(monkeypatch)
    monkeypatch.setattr(main_module, "_script_context_use_case", forbidden)
    monkeypatch.setattr(main_module, "_metadata_edit_use_case", forbidden)
    task = SimpleNamespace(
        id="task-one", stage="narration", confirmed_script_id="script-confirmed",
        narration_cache_key=None, current_timeline_version_id=None, montage_plan_id=None,
    )
    monkeypatch.setattr(main_module, "production_repo", SimpleNamespace())
    request = main_module.CreationTaskAssistantTurnRequest(
        conversation_id=uuid4(), message="按已确认文案开始制作", client_request_id=uuid4(), expected_draft_version=0,
        context_stage="narration", selected_object_refs=[],
    )
    assert main_module._assistant_turn_scope("local", task, request) == ("narration", [])

    selected_edit = main_module.CreationTaskAssistantTurnRequest(
        conversation_id=uuid4(), message="把镜头调亮", client_request_id=uuid4(), expected_draft_version=0,
        context_stage="montage", selected_object_refs=["clip:clip-one"],
    )
    with pytest.raises(HTTPException) as exc:
        main_module._assistant_turn_scope("local", task, selected_edit)
    assert exc.value.detail == "当前任务尚未生成可修改的故事板"


def test_script_draft_stays_unconfirmed_and_assistant_history_is_persisted(tmp_path, monkeypatch):
    import json
    import httpx
    from test_creation_task_topic_provider import _review_tool_response
    from app.creation.topic_contracts import (
        TopicCandidate,
    )
    from app.topics import CreationTaskTopicSource, OpenAICompatibleCreationTaskScriptGenerator

    repo = _script_task(tmp_path)
    conversations, member_id, token, conversation = _assistant_task_session(tmp_path, monkeypatch, "task-one")
    topic = TopicCandidate(id="topic-one", workspace_id="local", task_id="task-one", run_id="fixture-topic-run",
        title="测试主题", angle="讲清楚", audience="普通人", outline=["开头"], score=90)
    source = CreationTaskTopicSource(reference_id="knowledge:one:v1", kind="knowledge", title="已授权资料",
        text="资料用于介绍测试主题；可以把开头缩短，用更短的句子讲清楚主题。")
    review_requests = []
    def review(request):
        payload = json.loads(request.content)
        review_requests.append(payload)
        candidate = json.loads(payload["messages"][1]["content"])["script"]
        response = {"sections": [{"index": index,
            "supports": [{"candidate_quote_id": section["content"][0]["quote_id"],
                          "source_quote_id": json.loads(payload["messages"][1]["content"])["sources"][0]["text"][0]["quote_id"]}]}
            for index, section in enumerate(candidate["sections"], start=1)]}
        return _review_tool_response(response)
    checker = OpenAICompatibleCreationTaskScriptGenerator("https://models.invalid/v1", "controlled-review",
        transport=httpx.MockTransport(review))
    monkeypatch.setattr(main_module, "_creation_task_script_generator_for", lambda settings: checker)

    def inputs(_case, workspace_id, task_id):
        task = main_module._creation_task_or_404(workspace_id, task_id)
        if task.stage != "script" or task.confirmed_script_id:
            raise HTTPException(status_code=409, detail="当前任务不在待确认文案阶段")
        plan = repo.plan_records.get_creation_plan(workspace_id, task.plan_id)
        plan.knowledge_references = [CreationPlanKnowledgeReference(
            id="one", version="v1", title=source.title, authorization="available", ownership="workspace",
        )]
        return task, plan, topic, (source,), [{
            "reference_id": source.reference_id, "kind": "knowledge", "object_id": "one",
            "version_or_identity": "v1", "title": source.title,
        }]

    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module.ScriptContextUseCase, "read_inputs", inputs)
    monkeypatch.setattr(main_module, "source_repository", SimpleNamespace(
        get_knowledge_reference_readable_by=lambda *_args, **_kwargs: SimpleNamespace(
            version="v1", title="已授权资料", authorization="available",
            ownership="workspace", use_state="available",
        ),
    ))
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
    assert repo.script_records.list_creation_task_scripts("local", "task-one") == []

    first = client.post(base + "/script-draft", json=body)
    assert first.status_code == 200
    script_id = first.json()["id"]
    assert first.json()["selection_status"] == "draft"
    assert repo.task_records.get_creation_task("local", "task-one").confirmed_script_id is None
    second = client.post(base + "/script-draft", json={
        **body, "edit_mode": "replace_all", "sections": [{**body["sections"][0], "content": "把开头缩短。"}],
        "expected_updated_at": first.json()["updated_at"],
    })
    assert second.status_code == 200 and second.json()["id"] == script_id
    assert len(repo.script_records.list_creation_task_scripts("local", "task-one")) == 1
    stale = client.post(base + "/script-draft", json={
        **body, "edit_mode": "replace_all",
        "sections": [{**body["sections"][0], "content": "迟到的旧版本"}],
        "expected_updated_at": first.json()["updated_at"],
    })
    assert stale.status_code == 409
    assert len(review_requests) == 2  # First draft and revision; stale CAS never calls the model.
    assert repo.script_records.get_creation_task_script("local", script_id).sections[0].content == "把开头缩短。"

    def run_turn(_settings, **kwargs):
        if kwargs["stage"] == "script":
            return "草稿已更新，请检查后确认。", {"script_id": script_id}
        assert kwargs["stage"] == "narration"
        assert kwargs["selected_refs"] == []
        return "可以继续讨论当前已确认文案。", None

    monkeypatch.setattr(main_module, "run_task_turn", run_turn)
    turn = client.post(base + "/turn", headers={"X-Local-Member-Token": token}, json=
                       _task_turn_payload(conversations, member_id, conversation, "把开头缩短", "script"))
    assert turn.status_code == 200
    assert turn.json()["script_draft"]["id"] == script_id
    assert turn.json()["edit_preview"] is None
    persisted = ProductionRepository(tmp_path / "isolated.sqlite3", tmp_path / "library")
    events = persisted.execution_records.list_task_events("local", "task-one")
    assert not [item for item in events if item.event_type == "assistant_turn"]
    saved_turns = conversations.list_turns(conversation["id"])
    assert [(item["user_message"], item["assistant_text"], item["status"]) for item in saved_turns] == [
        ("把开头缩短", "草稿已更新，请检查后确认。", "completed"),
    ]
    history = client.get(base + "/history")
    assert history.status_code == 200
    assert history.json()["items"] == []  # New assistant turns have one writer; this remains the legacy read-only projection.
    assert "private-test-key" not in history.text
    rejected_secret = client.post(base + "/turn", headers={"X-Local-Member-Token": token}, json=
                                  _task_turn_payload(conversations, member_id, conversation,
                                                     "api_key=private-test-key 请加到文案", "script"))
    assert rejected_secret.status_code == 422
    assert not [item for item in repo.execution_records.list_task_events("local", "task-one") if item.event_type == "assistant_turn"]

    saved = repo.script_records.get_creation_task_script("local", script_id)
    repo.script_records.confirm_creation_task_script("local", "task-one", script_id,
                                      title=saved.title, sections=saved.sections,
                                      expected_updated_at=saved.updated_at)
    assert client.post(base + "/script-draft", json=body).status_code == 409
    for stage in ("script", "topic", "montage", "review", "subtitle_cover"):
        response = client.post(base + "/turn", headers={"X-Local-Member-Token": token}, json=
                               _task_turn_payload(conversations, member_id, conversation, "继续修改", stage))
        assert response.status_code == 409, stage

    narration_turn = client.post(base + "/turn", headers={"X-Local-Member-Token": token}, json=
                                 _task_turn_payload(conversations, member_id, conversation, "继续讨论", "narration"))
    assert narration_turn.status_code == 200, narration_turn.text
    assert narration_turn.json()["script_draft"] is None
    assert narration_turn.json()["edit_preview"] is None
    persisted_after_narration = ProductionRepository(tmp_path / "isolated.sqlite3", tmp_path / "library")
    events = persisted_after_narration.execution_records.list_task_events("local", "task-one")
    assert not [item for item in events if item.event_type == "assistant_turn"]
    saved_turns = conversations.list_turns(conversation["id"])
    assert [(item["user_message"], item["assistant_text"], item["status"]) for item in saved_turns] == [
        ("把开头缩短", "草稿已更新，请检查后确认。", "completed"),
        ("继续讨论", "可以继续讨论当前已确认文案。", "completed"),
    ]
    history = client.get(base + "/history")
    assert history.status_code == 200
    assert history.json()["items"] == []
    assert repo.task_records.get_creation_task("local", "task-one").confirmed_script_id == script_id
