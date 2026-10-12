from dataclasses import asdict, replace
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.production import ProductionRepository, VideoTemplate
from app.source_repository import SourceRepository
from app.sources import SourceConnector, SourceItem, KnowledgeItem
from app.topics import OpenAICompatibleCreationTaskScriptGenerator, CreationTaskScriptGenerationError
from test_production_foundation import _repository, _missing_model_catalog, plan_owner as plan_owner
from test_creation_task_topic_provider import _script_input, _script_document, _response
from _media_preview_test_fixtures import register_synthetic_profiles, register_synthetic_video_asset
from app.avatar_lipsync import PronunciationEntry as ProfilePronunciationEntry


@pytest.fixture
def state(tmp_path, monkeypatch, plan_owner):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    owner_id = plan_owner[0].member_id
    connector = SourceConnector(
        id="connector-workspace-a", workspace_id="workspace_a", platform="manual",
        protocol="manual", account_or_rule="operator", frequency="on_demand", permissions=("read",),
    )
    sources.upsert_connector(connector)
    source_item = SourceItem(
        id="source-workspace-a", workspace_id="workspace_a", connector_id=connector.id,
        source_label="manual", source_url="https://example.com/source", content_type="article",
        author="operator", published_at=None, body="真实信息源正文。", subtitles=None,
        media_references=(), license="authorized", title="信息源条目",
    )
    sources.save_source_items("workspace_a", [source_item])
    sources.create_knowledge_item_owned(KnowledgeItem(
        id="knowledge-workspace-a", workspace_id="workspace_a", content_type="note",
        title="用户知识", body="用户拥有并授权的知识内容。", file_reference=None,
        authorization="authorized", ownership="user_owned", source_item_id=source_item.id, version="v1",
    ), verified_user_id=owner_id)
    person, voice, _avatar_authorization, _voice_path, _image_path = register_synthetic_profiles(
        production, workspace_id="workspace_a", member_id=owner_id,
        person_id="voice-workspace_a-person", voice_id="voice-workspace_a",
        person_version="v1", voice_version="v1", language="zh", model_id="test-voice-model",
    )
    asset, _asset_reference = register_synthetic_video_asset(
        production, workspace_id="workspace_a", member_id=owner_id,
        path=production.video_library_path / "workspace_a.mp4", asset_id="asset-workspace-a", seconds=25,
    )
    asset.segments[0].description = "完全不同的新主题"
    production._save_asset(asset)
    production.save_pronunciation_entry(ProfilePronunciationEntry(
        id="pronunciation-workspace-a", workspace_id="workspace_a", term="Codex",
        language="zh", target_pronunciation="科德克斯", scope="person",
        scope_reference=voice.person_profile_id, version="v1",
    ))
    monkeypatch.setattr(main, "production_repo", production)
    monkeypatch.setattr(main, "source_repository", sources)
    monkeypatch.setattr(main, "model_catalog", _missing_model_catalog(tmp_path))
    monkeypatch.setattr(main, "current_workspace_id", lambda request: request.headers.get("x-test-workspace", "workspace_a"))
    template = VideoTemplate(
        id="template_test", workspace_id="workspace_a", version=1, name="测试设置", origin="user",
        settings={"language": "zh-CN", "aspect_ratio": "9:16", "duration_target_seconds": 30,
                  "voice": {"profile_id": "voice-workspace_a", "version": "v1"},
                  "script_section_count": 2, "style_instruction": "先结论后步骤", "fit_mode": "fit", "subtitle_style": {"position": "top"},
                  "color_style": {"exposure": 0.5, "contrast": 1, "saturation": 0.8, "temperature": 0.15}},
    )
    # 模板归属只认可信身份：这批用例测版本追加与持久化，因此用真实成员 id 创建，
    # 仓储内部读取时显式给出同一读取身份，而不是靠匿名或改可见范围绕过。
    template = production.save_video_template(template, verified_owner_user_id=plan_owner[0].member_id)
    return production, template, TestClient(main.app, headers=plan_owner[1])


def test_versions_are_append_only_workspace_scoped_and_survive_restart(state):
    production, template, client = state
    with pytest.raises(ValueError, match="版本已变化"):
        production.save_video_template(template)
    production.save_video_template(replace(template, version=2, name="新版"))
    reopened = ProductionRepository(production.database_path, production.video_library_path)
    assert reopened.get_video_template("workspace_a", template.id, 1).name == template.name
    # 仓库级读取按创建时的可信身份给出；跨工作区隔离仍要单独成立。
    listed = reopened.list_video_templates(
        "workspace_a", viewer_user_id=template.owner_user_id
    )
    assert len(listed) == 2
    # 同一个创建者身份在另一个工作区读不到：空结果来自跨空间隔离，
    # 不是来自「没给身份所以按 owner 过滤掉了」。
    assert reopened.list_video_templates(
        "workspace_b", viewer_user_id=template.owner_user_id
    ) == []
    response = client.post("/api/creation-plans/preview", headers={"x-test-workspace": "workspace_b"}, json={"goal": "新主题", "template_id": template.id, "template_version": 1})
    assert response.status_code == 404


def test_preflight_preserves_exact_voice_version_and_new_plan_snapshot(state):
    production, template, client = state
    voice = production.get_versioned_voice_profile("workspace_a", "voice-workspace_a", "v1")
    production.save_versioned_voice_profile(
        replace(voice, version="v2"), verified_owner_user_id=template.owner_user_id
    )
    payload = {"goal": "完全不同的新主题", "template_id": template.id, "template_version": 1}
    preview = client.post("/api/creation-plans/preview", json=payload).json()
    assert preview["selected_voice"]["version"] == "v1"
    assert preview["duration_target_seconds"] == 30
    assert preview["template_settings"] == template.settings
    response = client.post("/api/creation-plans/confirm", json={**payload, "preflight_fingerprint": preview["preflight_fingerprint"]})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["creation_task"]["selected_topic_id"] is None
    assert result["creation_task"]["output_ready"] is False
    before = asdict(production.get_creation_plan("workspace_a", result["id"]))
    production.save_video_template(replace(template, version=2, settings={**template.settings, "duration_target_seconds": 45}))
    assert asdict(production.get_creation_plan("workspace_a", result["id"])) == before
    changed = client.post("/api/creation-plans/confirm", json={**payload, "template_version": 2, "preflight_fingerprint": preview["preflight_fingerprint"]})
    assert changed.status_code == 409


def test_missing_template_voice_blocks_without_substitution(state):
    production, template, client = state
    production.save_video_template(replace(template, version=2, settings={**template.settings, "voice": {"profile_id": "missing", "version": "v9"}}))
    preview = client.post("/api/creation-plans/preview", json={"goal": "新主题", "template_id": template.id, "template_version": 2}).json()
    assert preview["selected_voice"] is None
    assert not preview["confirmable"]
    assert any(item["reason"] == "template_voice_unavailable" and "v9" in item["impact"] for item in preview["blockers"])


def test_template_share_is_default_and_slider_choice_rechecks_plan(state):
    production, template, client = state
    production.save_video_template(replace(template, version=2, settings={**template.settings, "presenter_share_target": 40}))
    base = {"goal": "完全不同的新主题", "template_id": template.id, "template_version": 2}
    inherited = client.post("/api/creation-plans/preview", json=base)
    chosen = client.post("/api/creation-plans/preview", json={**base, "presenter_share_target": 20})
    assert inherited.status_code == chosen.status_code == 200
    assert inherited.json()["presenter_share_target"] == 40
    assert chosen.json()["presenter_share_target"] == 20
    assert inherited.json()["preflight_fingerprint"] != chosen.json()["preflight_fingerprint"]


def test_lipsync_quality_defaults_and_stays_in_versioned_plan(state):
    production, template, client = state
    goal = "完全不同的新主题"
    new_preview = client.post("/api/creation-plans/preview", json={"goal": goal}).json()
    fast_preview = client.post("/api/creation-plans/preview", json={"goal": goal, "lipsync_quality": "fast"}).json()
    assert new_preview["lipsync_quality"] == "high"
    assert fast_preview["lipsync_quality"] == "fast"
    assert new_preview["preflight_fingerprint"] != fast_preview["preflight_fingerprint"]
    assert any(item["id"].startswith("latentsync-") for item in new_preview["model_snapshot"])
    assert any(item["id"] == main.avatar_lipsync_provider.model_id for item in fast_preview["model_snapshot"])

    # Templates saved before the selector existed keep the former MuseTalk path.
    payload = {"goal": goal, "template_id": template.id, "template_version": 1}
    legacy = client.post("/api/creation-plans/preview", json=payload).json()
    assert legacy["lipsync_quality"] == "fast"
    confirmed = client.post("/api/creation-plans/confirm", json={**payload, "preflight_fingerprint": legacy["preflight_fingerprint"]})
    assert confirmed.status_code == 201, confirmed.text
    assert production.get_creation_plan("workspace_a", confirmed.json()["id"]).lipsync_quality == "fast"

    production.save_video_template(replace(template, version=2, settings={**template.settings, "lipsync_quality": "high"}))
    inherited = client.post("/api/creation-plans/preview", json={**payload, "template_version": 2}).json()
    assert inherited["lipsync_quality"] == "high"
    assert inherited["preflight_fingerprint"] != legacy["preflight_fingerprint"]


def test_save_unrendered_task_is_conflict_not_server_error(state):
    _, _, client = state
    payload = {"goal": "尚未制作的视频"}
    preview = client.post("/api/creation-plans/preview", json=payload).json()
    task = client.post("/api/creation-plans/confirm", json={**payload, "preflight_fingerprint": preview["preflight_fingerprint"]}).json()["creation_task"]
    response = client.post(f"/api/creation-tasks/{task['id']}/templates", json={"name": "不能保存"})
    assert response.status_code == 409


def test_script_consumes_style_and_structure_and_rejects_wrong_section_count():
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return _response(_script_document(evidence_ids=["evidence_1"]))
    generator = OpenAICompatibleCreationTaskScriptGenerator(base_url="https://model.invalid/v1", model="test", transport=httpx.MockTransport(respond))
    source = replace(_script_input(), style_instruction="先结论后步骤", script_structure=("结论", "步骤"), script_section_count=2)
    with pytest.raises(CreationTaskScriptGenerationError, match="段落数量"):
        generator.generate(source)
    prompt = json.loads(requests[0]["messages"][1]["content"])
    assert prompt["style_instruction"] == source.style_instruction
    assert prompt["script_structure"] == ["结论", "步骤"]
    assert prompt["requirements"]["section_count"] == 2
    assert "sections 必须恰好有 2 项" in requests[0]["messages"][0]["content"]
    assert "同一段落可以包含多个短句" in requests[0]["messages"][0]["content"]


def test_template_preflight_does_not_substitute_unrelated_materials(state, monkeypatch):
    production, template, client = state
    calls = []
    def search(workspace_id, **kwargs):
        calls.append(kwargs)
        return []
    monkeypatch.setattr(production, "search_material_segments", search)
    result = client.post("/api/creation-plans/preview", json={"goal": "新主题没有匹配片段", "template_id": template.id, "template_version": 1}).json()
    assert len(calls) == 1 and calls[0]["query"] == "新主题没有匹配片段"
    assert result["material_matches"] == []
    assert not result["confirmable"]
    assert any(item["reason"] == "template_material_matches_missing" for item in result["blockers"])


@pytest.mark.parametrize("language,text", [
    ("zh-CN", "字" * 48),
    ("en-US", "word " * 30),
    ("zh-CN", "字" * 24 + " word" * 15),
])
def test_script_rejects_total_speech_duration_across_sections(language, text):
    document = _script_document(evidence_ids=["evidence_1"])
    document["script"]["language"] = language
    section = document["script"]["sections"][0]
    document["script"]["sections"] = [{**section, "content": text}, {**section, "content": text}]
    generator = OpenAICompatibleCreationTaskScriptGenerator(
        base_url="https://model.invalid/v1", model="test",
        transport=httpx.MockTransport(lambda request: _response(document)),
    )
    # Each section is 12 seconds, but the shared total exceeds the 20-second plan.
    with pytest.raises(CreationTaskScriptGenerationError, match="24.0 秒"):
        generator.generate(replace(_script_input(), language=language, duration_target_seconds=20))


@pytest.mark.parametrize('ratio',['16:9','1:1'])
def test_template_canvas_survives_new_theme_preflight_and_plan_confirmation(state,ratio):
    production,template,client=state
    production.save_video_template(replace(template,version=2,settings={**template.settings,'aspect_ratio':ratio}))
    payload={'goal':'完全不同的新主题','template_id':template.id,'template_version':2}
    preview=client.post('/api/creation-plans/preview',json=payload)
    assert preview.status_code==200,preview.text
    assert preview.json()['aspect_ratio']==ratio
    confirmed=client.post('/api/creation-plans/confirm',json={**payload,'preflight_fingerprint':preview.json()['preflight_fingerprint']})
    assert confirmed.status_code==201,confirmed.text
    assert confirmed.json()['aspect_ratio']==ratio
    assert not confirmed.json()['creation_task']['output_ready']


def test_template_field_version_endpoint_is_append_only_and_preserves_real_settings(state):
    production, template, client = state
    template = replace(template, source_task_id="fixture-source-task", source_timeline_version_id="fixture-timeline")
    # Establish immutable provenance before this endpoint is used.
    template = production.save_video_template(replace(template, version=2))
    payload = {"expected_version": 2, "name": " 新名称 ", "style_instruction": " 新风格 "}
    response = client.post(f"/api/video-templates/{template.id}/versions", json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["version"] == 3 and saved["name"] == "新名称" and saved["can_edit"] is True
    assert saved["settings"] == {**template.settings, "style_instruction": "新风格"}
    assert saved["source_task_id"] == template.source_task_id
    assert saved["source_timeline_version_id"] == template.source_timeline_version_id
    assert saved["owner_user_id"] == template.owner_user_id
    assert saved["visibility"] == template.visibility
    assert client.post(f"/api/video-templates/{template.id}/versions", json=payload).status_code == 409
    reopened = ProductionRepository(production.database_path, production.video_library_path)
    assert reopened.get_video_template("workspace_a", template.id, 2) == template
    assert reopened.get_video_template("workspace_a", template.id, 3).settings == saved["settings"]
    listed = client.get("/api/video-templates").json()["items"]
    assert {item["version"] for item in listed if item["id"] == template.id} == {1, 2, 3}
    assert all(item["can_edit"] for item in listed if item["id"] == template.id)
    blank_style = client.post(f"/api/video-templates/{template.id}/versions", json={"expected_version": 3, "name": "名称", "style_instruction": ""})
    assert blank_style.status_code == 200 and blank_style.json()["settings"]["style_instruction"] == ""


def test_template_field_version_endpoint_requires_actual_owner_and_visible_workspace(state):
    production, template, client = state
    _member, token = main.local_members_service.join(main.local_members_service.create_invite_code(), "模板只读成员")
    reader = TestClient(main.app, headers={"X-Local-Member-Token": token})
    payload = {"expected_version": 1, "name": "尝试修改", "style_instruction": ""}
    path = f"/api/video-templates/{template.id}/versions"
    assert reader.post(path, json=payload).status_code == 404
    production.set_video_template_visibility("workspace_a", template.id, "shared")
    assert reader.post(path, json=payload).status_code == 403
    shared = reader.get("/api/video-templates").json()["items"]
    assert next(item for item in shared if item["id"] == template.id)["can_edit"] is False
    assert client.post(path, headers={"x-test-workspace": "workspace_b"}, json=payload).status_code == 404
    builtin = next(item for item in client.get("/api/video-templates").json()["items"] if item["origin"] == "built_in")
    assert builtin["can_edit"] is False
    assert client.post(f"/api/video-templates/{builtin['id']}/versions", json={**payload, "expected_version": builtin["version"]}).status_code == 409
    assert len(production.list_video_templates("workspace_a", viewer_user_id=template.owner_user_id)) == 1


@pytest.mark.parametrize("field,value", [("name", " "), ("name", "名" * 81), ("style_instruction", "字" * 501), ("expected_version", 0)])
def test_template_field_version_endpoint_rejects_invalid_fields_without_writing(state, field, value):
    production, template, client = state
    response = client.post(f"/api/video-templates/{template.id}/versions", json={"expected_version": 1, "name": "名称", "style_instruction": "", field: value})
    assert response.status_code == 422
    assert len(production.list_video_templates("workspace_a", viewer_user_id=template.owner_user_id)) == 1


def test_shared_template_reader_cannot_append_through_existing_task_save(state, monkeypatch, tmp_path):
    from types import SimpleNamespace
    production, template, _client = state
    _member, token = main.local_members_service.join(main.local_members_service.create_invite_code(), "任务模板只读成员")
    production.set_video_template_visibility("workspace_a", template.id, "shared")
    reader = TestClient(main.app, headers={"X-Local-Member-Token": token})
    # This focused permission test substitutes the rendered-task prerequisites only;
    # the template, trusted member, HTTP guard and immutable versions are real.
    task = SimpleNamespace(id="fixture-rendered-task", current_timeline_version_id="fixture-timeline")
    output = tmp_path / "fixture-output"
    output.write_bytes(b"nonempty fixture; no generated video")
    monkeypatch.setattr(main, "_creation_task_or_404", lambda *_args: task)
    monkeypatch.setattr(main, "_creation_plan_for_task_or_404", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(production, "get_creation_task_montage_plan", lambda *_args: {})
    monkeypatch.setattr(main, "creation_task_montage_plan_from_payload", lambda _payload: SimpleNamespace(status="rendered", output_path=str(output)))
    monkeypatch.setattr(main, "_creation_task_output_path", lambda *_args, **_kwargs: output)
    response = reader.post(f"/api/creation-tasks/{task.id}/templates", json={"name": "不能另增他人模板", "template_id": template.id, "expected_version": 1})
    assert response.status_code == 403, response.text
    assert "所有者" in response.json()["detail"]
    assert len(production.list_video_templates("workspace_a", viewer_user_id=template.owner_user_id)) == 1


def test_template_private_preflight_and_visibility_changes_require_current_owner(state):
    production, template, owner = state
    _member, token = main.local_members_service.join(main.local_members_service.create_invite_code(), "模板读取者")
    reader = TestClient(main.app, headers={"X-Local-Member-Token": token})
    payload = {"goal": "完全不同的新主题", "template_id": template.id, "template_version": 1}
    path = f"/api/video-templates/{template.id}/visibility"
    assert reader.post("/api/creation-plans/preview", json=payload).status_code == 404
    assert reader.put(path, json={"visibility": "shared"}).status_code == 403
    assert owner.put(path, json={"visibility": "shared"}).status_code == 200
    assert reader.post("/api/creation-plans/preview", json=payload).status_code == 200
    assert reader.put(path, json={"visibility": "private"}).status_code == 403
    assert production.get_video_template("workspace_a", template.id, 1).visibility == "shared"
    assert owner.put(path, json={"visibility": "private"}).status_code == 200
    assert reader.post("/api/creation-plans/preview", json=payload).status_code == 404


def test_explicit_knowledge_pin_preserves_history_and_rechecks_current_access(state):
    production, template, client = state
    sources = main.source_repository
    item = sources.create_knowledge_item_owned(KnowledgeItem(
        id="knowledge-fixed", workspace_id="workspace_a", content_type="note",
        title="原来的资料", body="重量: 20 kg", file_reference=None,
        authorization="authorized", ownership="user_owned", source_item_id=None, version="1",
    ), verified_user_id=template.owner_user_id)
    sources.edit_knowledge_item("workspace_a", item.id, expected_version="1", title="修订资料", body="重量: 25 kg", verified_user_id=template.owner_user_id)
    payload = {"goal": "完全不同的新主题", "selected_knowledge": {"knowledge_item_id": item.id, "version": "1"}}
    response = client.post("/api/creation-plans/preview", json=payload)
    assert response.status_code == 200, response.text
    preview = response.json()
    pinned = next(ref for ref in preview["knowledge_references"] if ref["id"] == item.id)
    assert pinned["version"] == "1" and pinned["title"] == "原来的资料"
    assert sum(ref["id"] == item.id for ref in preview["knowledge_references"]) == 1
    confirmed = client.post("/api/creation-plans/confirm", json={**payload, "preflight_fingerprint": preview["preflight_fingerprint"]})
    assert confirmed.status_code == 201, confirmed.text
    persisted = ProductionRepository(production.database_path, production.video_library_path).get_creation_plan("workspace_a", confirmed.json()["id"])
    assert next(ref for ref in persisted.knowledge_references if ref.id == item.id).version == "1"
    assert not confirmed.json()["creation_task"]["output_ready"]
    assert client.post("/api/creation-plans/preview", headers={"x-test-workspace": "workspace_b"}, json=payload).status_code == 404
    assert client.post("/api/creation-plans/preview", json={**payload, "selected_knowledge": {**payload["selected_knowledge"], "body": "伪造正文"}}).status_code == 422
    _member, token = main.local_members_service.join(main.local_members_service.create_invite_code(), "知识读取者")
    reader = TestClient(main.app, headers={"X-Local-Member-Token": token})
    assert reader.post("/api/creation-plans/preview", json=payload).status_code == 404
    sources.set_knowledge_item_visibility("workspace_a", item.id, "shared")
    assert reader.post("/api/creation-plans/preview", json=payload).status_code == 200
    sources.set_knowledge_item_visibility("workspace_a", item.id, "private")
    assert reader.post("/api/creation-plans/preview", json=payload).status_code == 404
    sources.set_knowledge_item_use_state("workspace_a", item.id, "not_for_automation")
    assert client.post("/api/creation-plans/preview", json=payload).status_code == 200
    sources.set_knowledge_item_use_state("workspace_a", item.id, "excluded")
    blocked = client.post("/api/creation-plans/confirm", json={**payload, "preflight_fingerprint": preview["preflight_fingerprint"]})
    assert blocked.status_code == 404, blocked.text
    assert len(production.list_creation_tasks("workspace_a", viewer_user_id=template.owner_user_id)) == 1
