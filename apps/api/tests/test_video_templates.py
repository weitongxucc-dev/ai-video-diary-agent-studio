from dataclasses import asdict, replace
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.production import ProductionRepository, VideoTemplate
from app.source_repository import SourceRepository
from app.topics import OpenAICompatibleCreationTaskScriptGenerator, CreationTaskScriptGenerationError
from test_production_foundation import _repository, _seed_creation_plan_facts, _missing_model_catalog, _save_versioned_voice
from test_creation_task_topic_provider import _script_input, _script_document, _response


@pytest.fixture
def state(tmp_path, monkeypatch):
    production = _repository(tmp_path)
    sources = SourceRepository(production.database_path)
    _seed_creation_plan_facts(production, sources, workspace_id="workspace_a")
    asset = production.list_assets("workspace_a")[0]
    asset.segments[0].description = "完全不同的新主题"
    production._save_asset(asset)
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
    production.save_video_template(template)
    return production, template, TestClient(main.app)


def test_versions_are_append_only_workspace_scoped_and_survive_restart(state):
    production, template, client = state
    with pytest.raises(ValueError, match="版本已变化"):
        production.save_video_template(template)
    production.save_video_template(replace(template, version=2, name="新版"))
    reopened = ProductionRepository(production.database_path, production.video_library_path)
    assert reopened.get_video_template("workspace_a", template.id, 1).name == template.name
    assert len(reopened.list_video_templates("workspace_a")) == 2
    assert reopened.list_video_templates("workspace_b") == []
    response = client.post("/api/creation-plans/preview", headers={"x-test-workspace": "workspace_b"}, json={"goal": "新主题", "template_id": template.id, "template_version": 1})
    assert response.status_code == 404


def test_preflight_preserves_exact_voice_version_and_new_plan_snapshot(state):
    production, template, client = state
    _save_versioned_voice(production, workspace_id="workspace_a", voice_id="voice-workspace_a", version="v2", reference_audio_path="workspace://approved/workspace_a.wav")
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
