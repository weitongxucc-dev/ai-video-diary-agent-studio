import json
import socket
from pathlib import Path

from app.config import Settings
from app.config import record_llm_service_verification
from app.model_runtime import CHINESE_CLIP_VIT_B16, ModelCatalog
from app.models import utc_now
from app.runtime_health import (
    _LOCAL_MODEL_FEATURES,
    _deepseek_item,
    _feature_gates,
    _local_model_item,
    _obsidian_item,
    runtime_health,
)


def test_startup_health_does_not_traverse_user_documents(tmp_path, monkeypatch):
    def blocked_scan(*args, **kwargs):
        raise AssertionError("Readiness must not scan nested user directories")
    monkeypatch.setattr(Path, "rglob", blocked_scan)
    item = _obsidian_item(Settings(obsidian_vault_path=str(tmp_path)), utc_now())
    assert item["status"] == "ready"
    assert "内容在读取资料时检查" in item["reason"]
    status = Settings(obsidian_vault_path=str(tmp_path)).safe_runtime_settings()["status"]
    assert status["obsidian"]["markdown_count"] is None


def test_closed_local_model_port_is_not_reported_available():
    # Binding without listening avoids a free-port race.
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        port = server.getsockname()[1]
        settings = Settings(llm_provider='custom', llm_base_url=f'http://127.0.0.1:{port}/v1', llm_model='local')
        item = _deepseek_item(settings, utc_now(), "health-unverified-closed-port")
    assert item['status'] == 'degraded'
    assert '还没有真的连过一次服务' in item['reason']
    assert '核对当前工作区设置' in item['repair_action']


def test_listening_port_does_not_claim_model_generation_verified():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen()
        port = server.getsockname()[1]
        settings = Settings(llm_provider='custom', llm_base_url=f'http://127.0.0.1:{port}/v1', llm_model='local')
        item = _deepseek_item(settings, utc_now(), "health-unverified-listening-port")
    assert item['status'] == 'degraded'
    assert '还没有真的连过一次服务' in item['reason']


def test_cloud_configuration_check_makes_no_network_request(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('Cloud health must not send a request')
    monkeypatch.setattr(socket, 'create_connection', unexpected)
    item = _deepseek_item(Settings(llm_provider='custom', llm_base_url='https://example.com/v1', llm_model='remote'), utc_now(), "health-unverified-cloud")
    assert item['status'] == 'degraded'
    assert '还没有真的连过一次服务' in item['reason']


def test_invalid_local_port_returns_actionable_health_result():
    item = _deepseek_item(Settings(llm_provider='custom', llm_base_url='http://127.0.0.1:bad/v1', llm_model='local'), utc_now(), "health-unverified-invalid-port")
    assert item['status'] == 'degraded'
    assert '还没有真的连过一次服务' in item['reason']


def test_recent_current_workspace_failure_is_reported_without_cross_workspace_reuse():
    settings = Settings(llm_provider='custom', llm_base_url='https://provider.example/v1', llm_model='synthetic-model')
    record_llm_service_verification(
        settings, ok=False, cause='provider_rejected', detail='最近一次受控调用被拒绝。',
        repair_action='检查此工作区的模型设置。', workspace_id='health-workspace-failed',
    )

    failed = _deepseek_item(settings, utc_now(), 'health-workspace-failed')
    other = _deepseek_item(settings, utc_now(), 'health-workspace-other')

    assert failed['status'] == 'degraded'
    assert failed['reason'] == '最近一次受控调用被拒绝。'
    # The underlying current-workspace status retains the exact repair fact.
    assert settings.llm_service_status('health-workspace-failed')['repair_action'] == '检查此工作区的模型设置。'
    assert '还没有真的连过一次服务' in other['reason']


def test_recent_success_is_available_only_for_the_workspace_that_was_verified():
    settings = Settings(llm_provider='custom', llm_base_url='https://provider.example/v1', llm_model='synthetic-model')
    record_llm_service_verification(
        settings, ok=True, cause='provider_reply', workspace_id='health-workspace-verified',
    )

    verified = _deepseek_item(settings, utc_now(), 'health-workspace-verified')
    other = _deepseek_item(settings, utc_now(), 'health-workspace-unverified')

    assert verified['status'] == 'ready'
    assert '上一次真实调用成功' in verified['reason']
    assert other['status'] == 'degraded'
    assert '还没有真的连过一次服务' in other['reason']


def _catalog_with_installed_weights(tmp_path, *, write_runtime=True):
    runtime_python = tmp_path / "python"
    worker = tmp_path / "worker.py"
    if write_runtime:
        runtime_python.write_text("python", encoding="utf-8")
    worker.write_text("worker", encoding="utf-8")
    catalog = ModelCatalog(tmp_path / "models", runtime_python, worker)
    model_path = catalog.model_path(CHINESE_CLIP_VIT_B16)
    model_path.mkdir(parents=True)
    weights = model_path / "model.safetensors"
    weights.write_bytes(b"weights")
    (model_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_id": CHINESE_CLIP_VIT_B16.id,
                "repository": CHINESE_CLIP_VIT_B16.repository,
                "revision": CHINESE_CLIP_VIT_B16.revision,
                "files": [{"path": weights.name, "size": weights.stat().st_size}],
            }
        ),
        encoding="utf-8",
    )
    return catalog


def test_absent_local_model_reports_missing_with_a_download_fix_command(tmp_path):
    catalog = ModelCatalog(tmp_path / "models", tmp_path / "python", tmp_path / "worker.py")
    _feature, spec, item_id, label, impact = next(
        entry
        for entry in _LOCAL_MODEL_FEATURES
        if entry[0] == "material-visual-semantics"
    )

    item = _local_model_item(catalog, spec, item_id, label, impact, utc_now())

    assert item["status"] == "missing"
    assert item["fix_command"] == "在应用内「设置 → 模型管理」中下载"
    # 降级必须可见：不能只给一个「missing」了事，要说清后果。
    assert "只按素材文字与时长推荐" in item["impact"]
    assert "权重未下载" in item["reason"]
    assert CHINESE_CLIP_VIT_B16.display_name in item["label"]


def test_broken_local_model_install_reports_invalid_not_missing(tmp_path):
    catalog = _catalog_with_installed_weights(tmp_path)
    (catalog.model_path(CHINESE_CLIP_VIT_B16) / "manifest.json").write_text("{", encoding="utf-8")

    item = _local_model_item(
        catalog, CHINESE_CLIP_VIT_B16, "local_visual_semantics", "本地画面语义模型", "影响推荐。", utc_now()
    )

    assert item["status"] == "invalid"
    assert item["fix_command"] is None
    assert "重新下载" in item["repair_action"]


def test_present_weights_with_missing_runtime_reports_degraded(tmp_path):
    """标签跟着权重的状态走：权重还在就不该劝用户重下几 GB。"""
    catalog = _catalog_with_installed_weights(tmp_path, write_runtime=False)

    item = _local_model_item(
        catalog, CHINESE_CLIP_VIT_B16, "local_visual_semantics", "本地画面语义模型", "影响推荐。", utc_now()
    )

    assert item["status"] == "degraded"
    assert "运行环境不完整" in item["reason"]
    assert "重新下载" not in item["repair_action"]


def test_fully_installed_local_model_reports_ready_without_impact(tmp_path):
    catalog = _catalog_with_installed_weights(tmp_path)

    item = _local_model_item(
        catalog, CHINESE_CLIP_VIT_B16, "local_visual_semantics", "本地画面语义模型", "影响推荐。", utc_now()
    )

    assert item["status"] == "ready"
    assert item["impact"] == "无影响。"
    assert item["fix_command"] is None


def test_feature_gates_expose_both_local_model_dependencies(tmp_path):
    catalog = ModelCatalog(tmp_path / "models", tmp_path / "python", tmp_path / "worker.py")

    pi_checks = [{"id": dependency, "status": "ready"}
                 for dependency in ("pi", "pi_node", "pi_tools")]
    gates = {gate["feature"]: gate for gate in _feature_gates(Settings(), catalog, pi_checks)}

    assert gates["material-visual-understanding"]["available"] is False
    assert gates["material-visual-semantics"]["available"] is False
    # 门禁必须给出原因，否则前端只能显示一个没有解释的红灯。
    assert "权重未下载" in gates["material-visual-understanding"]["reason"]
    assert "权重未下载" in gates["material-visual-semantics"]["reason"]


def test_runtime_health_counts_every_local_model_item(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.runtime_health.model_catalog_from_settings",
        lambda settings: ModelCatalog(tmp_path / "models", tmp_path / "python", tmp_path / "worker.py"),
    )

    report = runtime_health(Settings())
    by_id = {item["id"]: item for item in report["items"]}

    assert by_id["local_vlm"]["status"] == "missing"
    assert by_id["local_visual_semantics"]["status"] == "missing"
    assert report["summary"]["total"] == len(report["items"])
    assert report["summary"]["missing"] == sum(1 for item in report["items"] if item["status"] == "missing")
