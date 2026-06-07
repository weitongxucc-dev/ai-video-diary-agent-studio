from fastapi.testclient import TestClient

import app.main as main_module
from app.runtime_settings import runtime_settings_updates


def test_runtime_settings_updates_maps_known_fields_and_skips_blank_values():
    updates = runtime_settings_updates(
        {
            "deepseek_api_key": "   ",
            "deepseek_model": " deepseek-v4-flash ",
            "obsidian_vault_path": "/Users/example/Vault",
            "unknown_field": "ignored",
            "topic_source_urls": [
                " https://watcha.cn/ ",
                "",
                "https://example.com/feed",
            ],
        }
    )

    assert updates == {
        "DEEPSEEK_MODEL": "deepseek-v4-flash",
        "OBSIDIAN_VAULT_PATH": "/Users/example/Vault",
        "TOPIC_SOURCE_URLS": "https://watcha.cn/,https://example.com/feed",
    }


def test_runtime_settings_updates_omits_empty_topic_source_urls():
    updates = runtime_settings_updates(
        {
            "topic_source_urls": [" ", ""],
            "video_output_path": " /tmp/output ",
        }
    )

    assert updates == {"VIDEO_OUTPUT_PATH": "/tmp/output"}


def test_runtime_settings_endpoint_rejects_non_local_clients(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    reloaded = False

    def capture_reload():
        nonlocal reloaded
        reloaded = True

    monkeypatch.setattr(main_module, "runtime_env_path", env_path)
    monkeypatch.setattr(main_module, "_reload_runtime_dependencies", capture_reload)

    remote_client = TestClient(
        main_module.app,
        client=("203.0.113.10", 50000),
    )
    response = remote_client.post(
        "/api/settings/runtime",
        json={"deepseek_model": "deepseek-v4-flash"},
    )

    assert response.status_code == 403
    assert not env_path.exists()
    assert reloaded is False
