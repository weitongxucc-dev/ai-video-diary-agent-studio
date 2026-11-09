
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app import workspace_settings

client = TestClient(main_module.app)


@pytest.fixture(autouse=True)
def content_template_metadata_guard(request, monkeypatch, tmp_path):
    if request.node.originalname != 'test_content_templates_endpoint_returns_three_product_templates':
        return None
    import sqlite3
    import subprocess
    import wave
    from types import SimpleNamespace
    import httpx
    from PIL import Image
    from app.auth import SupabaseAuthContext
    from app.device_runtime import DeviceRuntime
    from app.device_scheduler import DeviceScheduler
    from app import host_resource_probes

    violations = []

    def forbidden(*args, **kwargs):
        violations.append('catalog-sql-execution-or-media')
        pytest.fail('catalog HTTP forbids databases, network, runtime, probes and media')

    # This pure catalog read needs no schema, member, repository or Store seed.
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    monkeypatch.setattr(host_resource_probes, 'sample_host_capacity', forbidden)
    monkeypatch.setattr(host_resource_probes.HostResourceProbe, '_sample', forbidden)
    for name in ('_creation_runtime', '_build_device_components', '_start_device_runtime',
                 '_device_operation_budget', '_workspace_settings_for', 'run_task_turn',
                 'run_plan_turn', 'start_assistant_continuation_driver'):
        monkeypatch.setattr(main_module, name, forbidden)
    monkeypatch.setattr(main_module, 'montage_renderer', SimpleNamespace(render=forbidden))
    monkeypatch.setattr(main_module, 'narration_generator', SimpleNamespace(generate_for_creation_task=forbidden))
    settings = Settings(database_path=str(tmp_path / 'unused.sqlite'),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'outputs'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main_module, 'settings', settings)
    monkeypatch.setattr(main_module, 'auth_context', SupabaseAuthContext(settings))
    for name in ('device_runtime', 'automation_runtime', 'production_repo', 'conversation_repo',
                 'source_repository', 'artifact_store', 'local_members_service', 'vault'):
        monkeypatch.setattr(main_module, name, None)

    def snapshot():
        return {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob('*')}

    before = snapshot()
    assert before == {}  # No unrelated persistence is needed to test the catalog.

    def assert_unchanged():
        assert snapshot() == before
        assert violations == []

    return SimpleNamespace(assert_unchanged=assert_unchanged)


def test_content_templates_endpoint_returns_three_product_templates(content_template_metadata_guard):
    response = client.get("/api/content-templates")

    assert response.status_code == 200
    body = response.json()
    template_ids = {template["template_id"] for template in body["templates"]}
    assert {"video_diary", "tool_review", "tutorial_demo"}.issubset(template_ids)
    for template in body["templates"]:
        assert template["name"]
        assert template["recommended_duration"]
        assert all(
            overlong not in template["recommended_duration"]
            for overlong in ("120", "180", "240")
        )
        assert template["topic_structure"]
        assert template["script_structure"]
        assert template["material_requirements"]
        assert template["jianying_style"]
        assert template["default_feedback_dimensions"]
    content_template_metadata_guard.assert_unchanged()




def test_runtime_health_endpoint_explains_current_workspace_status_and_repair_actions(tmp_path, monkeypatch):
    video_inbox = tmp_path / "inbox"
    video_output = tmp_path / "output"
    video_inbox.mkdir()
    video_output.mkdir()
    monkeypatch.setattr(main_module, "settings", Settings(
        deepseek_api_key=None,
        obsidian_vault_path=str(tmp_path / "missing-vault"),
        video_inbox_path=str(video_inbox),
        video_output_path=str(video_output),
        draft_output_path=str(tmp_path / "drafts"),
        jianying_draft_root_path=str(tmp_path / "jianying"),
        database_path=str(tmp_path / "agent.sqlite3"),
        topic_source_urls=("https://watcha.cn/",),
    ))
    workspace_id = "health-workspace-current"
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: workspace_id)
    workspace_settings.save_workspace_config(workspace_id, {
        "llm_provider": "custom",
        "llm_base_url": "https://provider.example/v1",
        "llm_model": "synthetic-health-model",
    })

    response = client.get("/api/runtime/health")

    assert response.status_code == 200
    body = response.json()
    items = {item["id"]: item for item in body["items"]}
    assert body["summary"]["total"] == len(body["items"])
    assert items["deepseek"]["status"] == "degraded"
    assert "还没有真的连过一次服务" in items["deepseek"]["reason"]
    assert items["deepseek"]["impact"]
    assert items["deepseek"]["repair_action"]
    assert items["obsidian"]["status"] == "missing"
    assert items["video_inbox"]["status"] == "ready"
    assert items["video_output"]["status"] == "ready"
    assert items["deepseek"]["last_checked_at"]
