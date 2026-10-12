from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main_module
from app.production import CreationPlan, CreationPlanKnowledgeReference, CreationTask, ProductionRepository
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
                       stage: str, refs: list[str] | None = None, *, playhead_ms=None):
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
    repo.confirm_creation_plan(plan, task)
    return repo


def test_script_turn_rejects_missing_authorized_sources_before_pi(tmp_path, monkeypatch):
    repo = _script_task(tmp_path)
    conversations, member_id, token, conversation = _assistant_task_session(tmp_path, monkeypatch, "task-one")
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(repo, "list_topic_candidates", lambda _workspace_id, _task_id: [
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
    assert repo.list_task_events("local", "task-one") == []


def test_script_turn_rejects_stale_sources_before_pi_or_events(tmp_path, monkeypatch):
    repo = _script_task(tmp_path)
    conversations, member_id, token, conversation = _assistant_task_session(tmp_path, monkeypatch, "task-one")
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
    response = TestClient(main_module.app).post(
        "/api/creation-tasks/task-one/assistant/turn", headers={"X-Local-Member-Token": token},
        json=_task_turn_payload(conversations, member_id, conversation, "请写开头", "script"),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "knowledge_reference_stale"
    assert repo.list_task_events("local", "task-one") == []


def test_review_task_allows_real_history_objects_and_rejects_foreign_refs(tmp_path, monkeypatch):
    from datetime import datetime
    from app.montage import MontageClip
    from app.production import MaterialAsset, MaterialSegment

    conversations, member_id, token, conversation = _assistant_task_session(tmp_path, monkeypatch, "task-one")
    material_repo = ProductionRepository(tmp_path / "selected-material.sqlite3", tmp_path / "selected-library")
    material_repo.video_library_path.mkdir()
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
    events = []
    calls = []
    task = SimpleNamespace(id="task-one", stage="review", confirmed_script_id="script-one",
                           narration_cache_key="narration-one", current_timeline_version_id=None,
                           montage_plan_id="plan-one", visibility="shared", owner_user_id=None)
    montage = SimpleNamespace(id="plan-one", task_id=task.id, script_id=task.confirmed_script_id,
                              status="rendered", output_path="rendered-video", duration_seconds=30,
                              clips=[clip], graphic_overlays=[])
    repo = SimpleNamespace(
        get_asset=material_repo.get_asset,
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
    headers = {"X-Local-Member-Token": token}
    for stage, ref in (("montage", "clip:clip-current"), ("review", "narration:segment-current")):
        response = client.post("/api/creation-tasks/task-one/assistant/turn", headers=headers, json=
                               _task_turn_payload(conversations, member_id, conversation, "请检查当前片段", stage, [ref]))
        assert response.status_code == 200, response.text
    for ref in ("clip:clip-from-other-task", "clip:clip-removed"):
        response = client.post("/api/creation-tasks/task-one/assistant/turn", headers=headers, json=
                               _task_turn_payload(conversations, member_id, conversation, "请检查旧镜头", "montage", [ref]))
        assert response.status_code == 422
    foreign = client.post("/api/creation-tasks/foreign-task/assistant/turn", headers=headers, json=
                          _task_turn_payload(conversations, member_id, conversation, "请检查当前片段", "montage"))
    assert foreign.status_code == 404
    task.stage = "narration"
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
    assert events == []  # Assistant turns live in the conversation repository, not legacy TaskEvent chat rows.
    saved_turns = conversations.list_turns(conversation["id"])
    assert [(item["user_message"], item["status"]) for item in saved_turns] == [
        ("请检查当前片段", "completed"), ("请检查当前片段", "completed"),
        ("请现在按当前已确认文案制作视频，并把真实排队状态显示给我", "completed"),
        ("开始制作，另外把字幕放大", "completed"),
    ]


def test_topic_scope_is_current_task_only():
    topic_task = SimpleNamespace(id="task-one", stage="topic")
    request = main_module.CreationTaskAssistantTurnRequest(
        conversation_id=uuid4(), message="请生成选题", client_request_id=uuid4(), expected_draft_version=0,
        context_stage="topic", selected_object_refs=[],
    )
    assert main_module._assistant_turn_scope("local", topic_task, request) == ("topic", [])


def test_confirmed_task_without_production_artifacts_can_use_general_assistant_scope(monkeypatch):
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
    from app.production import TopicCandidate
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
        response = {"sections": [{"index": index, "grounded": True, "constraints_met": True,
            "supports": [{"candidate_quote_id": section["content"][0]["quote_id"],
                          "source_quote_id": json.loads(payload["messages"][1]["content"])["sources"][0]["text"][0]["quote_id"]}], "issues": []}
            for index, section in enumerate(candidate["sections"], start=1)]}
        return _review_tool_response(response)
    checker = OpenAICompatibleCreationTaskScriptGenerator("https://models.invalid/v1", "controlled-review",
        transport=httpx.MockTransport(review))
    monkeypatch.setattr(main_module, "_creation_task_script_generator_for", lambda settings: checker)

    def inputs(workspace_id, task_id):
        task = main_module._creation_task_or_404(workspace_id, task_id)
        if task.stage != "script" or task.confirmed_script_id:
            raise HTTPException(status_code=409, detail="当前任务不在待确认文案阶段")
        plan = repo.get_creation_plan(workspace_id, task.plan_id)
        plan.knowledge_references = [CreationPlanKnowledgeReference(
            id="one", version="v1", title=source.title, authorization="available", ownership="workspace",
        )]
        return task, plan, topic, (source,), [{
            "reference_id": source.reference_id, "kind": "knowledge", "object_id": "one",
            "version_or_identity": "v1", "title": source.title,
        }]

    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "_assistant_script_inputs", inputs)
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
    assert repo.list_creation_task_scripts("local", "task-one") == []

    first = client.post(base + "/script-draft", json=body)
    assert first.status_code == 200
    script_id = first.json()["id"]
    assert first.json()["selection_status"] == "draft"
    assert repo.get_creation_task("local", "task-one").confirmed_script_id is None
    second = client.post(base + "/script-draft", json={
        **body, "sections": [{**body["sections"][0], "content": "把开头缩短。"}],
        "expected_updated_at": first.json()["updated_at"],
    })
    assert second.status_code == 200 and second.json()["id"] == script_id
    assert len(repo.list_creation_task_scripts("local", "task-one")) == 1
    stale = client.post(base + "/script-draft", json={
        **body,
        "sections": [{**body["sections"][0], "content": "迟到的旧版本"}],
        "expected_updated_at": first.json()["updated_at"],
    })
    assert stale.status_code == 409
    assert len(review_requests) == 2  # First draft and revision; stale CAS never calls the model.
    assert repo.get_creation_task_script("local", script_id).sections[0].content == "把开头缩短。"

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
    events = persisted.list_task_events("local", "task-one")
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
    assert not [item for item in repo.list_task_events("local", "task-one") if item.event_type == "assistant_turn"]

    saved = repo.get_creation_task_script("local", script_id)
    repo.confirm_creation_task_script("local", "task-one", script_id,
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
    events = persisted_after_narration.list_task_events("local", "task-one")
    assert not [item for item in events if item.event_type == "assistant_turn"]
    saved_turns = conversations.list_turns(conversation["id"])
    assert [(item["user_message"], item["assistant_text"], item["status"]) for item in saved_turns] == [
        ("把开头缩短", "草稿已更新，请检查后确认。", "completed"),
        ("继续讨论", "可以继续讨论当前已确认文案。", "completed"),
    ]
    history = client.get(base + "/history")
    assert history.status_code == 200
    assert history.json()["items"] == []
    assert repo.get_creation_task("local", "task-one").confirmed_script_id == script_id
