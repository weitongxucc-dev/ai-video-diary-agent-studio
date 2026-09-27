import socket
from pathlib import Path

from app.config import Settings
from app.models import utc_now
from app.runtime_health import _deepseek_item, _obsidian_item


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
        item = _deepseek_item(Settings(llm_provider='custom', llm_base_url=f'http://127.0.0.1:{port}/v1', llm_model='local'), utc_now())
    assert item['status'] == 'degraded'
    assert '无法连接' in item['reason']
    assert '启动本地模型服务' in item['repair_action']


def test_listening_port_does_not_claim_model_generation_verified():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen()
        port = server.getsockname()[1]
        item = _deepseek_item(Settings(llm_provider='custom', llm_base_url=f'http://127.0.0.1:{port}/v1', llm_model='local'), utc_now())
    assert item['status'] == 'ready'
    assert '端口可连接' in item['reason']
    assert '实际生成尚未验证' in item['reason']


def test_cloud_configuration_check_makes_no_network_request(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('Cloud health must not send a request')
    monkeypatch.setattr(socket, 'create_connection', unexpected)
    item = _deepseek_item(Settings(llm_provider='custom', llm_base_url='https://example.com/v1', llm_model='remote'), utc_now())
    assert item['status'] == 'ready'
    assert '连接与实际生成尚未验证' in item['reason']


def test_invalid_local_port_returns_actionable_health_result():
    item = _deepseek_item(Settings(llm_provider='custom', llm_base_url='http://127.0.0.1:bad/v1', llm_model='local'), utc_now())
    assert item['status'] == 'degraded'
    assert '端口' in item['repair_action']
