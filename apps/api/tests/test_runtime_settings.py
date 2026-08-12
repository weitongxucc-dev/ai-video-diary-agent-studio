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


def test_runtime_settings_updates_maps_draft_background_music_path():
    updates = runtime_settings_updates(
        {
            "draft_background_music_path": " /Users/example/bgm.mp3 ",
        }
    )
    assert updates == {"DRAFT_BACKGROUND_MUSIC_PATH": "/Users/example/bgm.mp3"}


def test_runtime_settings_response_includes_bgm_path_when_configured(tmp_path, monkeypatch):
    from app.config import Settings

    settings = Settings(draft_background_music_path="/tmp/bgm.mp3")
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "repo", main_module.repo)

    client = TestClient(main_module.app, client=("127.0.0.1", 50000))
    response = client.get("/api/settings/runtime")
    assert response.status_code == 200
    data = response.json()
    settings_entries = data["settings"]
    bgm_entry = next(
        (s for s in settings_entries if s["name"] == "DRAFT_BACKGROUND_MUSIC_PATH"),
        None,
    )
    assert bgm_entry is not None
    assert bgm_entry["configured"] is True
    assert bgm_entry["value"] == "/tmp/bgm.mp3"
    assert bgm_entry["secret"] is False


def test_runtime_settings_response_includes_bgm_path_when_unconfigured(tmp_path, monkeypatch):
    from app.config import Settings

    settings = Settings(draft_background_music_path=None)
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "repo", main_module.repo)

    client = TestClient(main_module.app, client=("127.0.0.1", 50000))
    response = client.get("/api/settings/runtime")
    assert response.status_code == 200
    data = response.json()
    settings_entries = data["settings"]
    bgm_entry = next(
        (s for s in settings_entries if s["name"] == "DRAFT_BACKGROUND_MUSIC_PATH"),
        None,
    )
    assert bgm_entry is not None
    assert bgm_entry["configured"] is False
    assert bgm_entry["value"] is None
