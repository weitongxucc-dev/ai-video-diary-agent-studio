from fastapi.testclient import TestClient

import app.main as main_module
from app.runtime_settings import runtime_settings_updates


def test_runtime_settings_updates_maps_known_fields_and_skips_blank_values():
    updates = runtime_settings_updates(
        {
            "deepseek_api_key": "   ",
            "deepseek_model": " deepseek-v4-flash ",
            "obsidian_vault_path": "/Users/example/Vault",
            "model_root_path": "/Volumes/Models/AI视频日记/models",
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
        "MODEL_ROOT_PATH": "/Volumes/Models/AI视频日记/models",
        "TOPIC_SOURCE_URLS": "https://watcha.cn/,https://example.com/feed",
    }


def test_runtime_settings_supports_openai_compatible_provider_fields():
    updates = runtime_settings_updates(
        {
            "llm_provider": " openrouter ",
            "llm_api_key": " sk-or-secret ",
            "llm_base_url": " https://openrouter.ai/api/v1 ",
            "llm_model": " anthropic/claude-sonnet-4.6 ",
            "temporary_root_path": " /Volumes/Media/AI视频日记/temporary ",
        }
    )

    assert updates == {
        "LLM_PROVIDER": "openrouter",
        "LLM_API_KEY": "sk-or-secret",
        "LLM_API_KEY_PROVIDER": "openrouter",
        "LLM_BASE_URL": "https://openrouter.ai/api/v1",
        "LLM_MODEL": "anthropic/claude-sonnet-4.6",
        "TEMPORARY_ROOT_PATH": "/Volumes/Media/AI视频日记/temporary",
    }


def test_model_runtime_defaults_follow_configured_model_root(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("MODEL_ROOT_PATH", "/Volumes/Models/AI视频日记/models")
    monkeypatch.delenv("MODEL_RUNTIME_PYTHON_PATH", raising=False)

    settings = Settings.from_env()

    assert settings.model_root_path == "/Volumes/Models/AI视频日记/models"
    assert (
        settings.model_runtime_python_path
        == "/Volumes/Models/AI视频日记/model-runtime/bin/python"
    )


def test_unconfigured_missing_model_root_keeps_operating_system_temp(tmp_path, monkeypatch):
    import tempfile

    from app.config import Settings, configure_process_temporary_directory

    original_tempdir = tempfile.tempdir
    monkeypatch.delenv("TMPDIR", raising=False)
    tempfile.tempdir = None
    try:
        selected = configure_process_temporary_directory(
            Settings(model_root_path=str(tmp_path / "missing" / "models"))
        )
        assert selected.is_dir()
        assert "missing" not in str(selected)
    finally:
        tempfile.tempdir = original_tempdir


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


def test_local_web_origins_can_call_the_api_but_remote_origins_cannot():
    client = TestClient(main_module.app, client=("127.0.0.1", 50000))
    headers = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }

    for origin in ("http://localhost:4179", "http://127.0.0.1:4179"):
        response = client.options(
            "/api/creation-tasks/example/edits/preview",
            headers={**headers, "Origin": origin},
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin

    response = client.options(
        "/api/creation-tasks/example/edits/preview",
        headers={**headers, "Origin": "https://example.com"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


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


def test_runtime_settings_endpoint_persists_and_returns_bgm_path(tmp_path, monkeypatch):
    """POST /api/settings/runtime accepts draft_background_music_path,
    persists it to the runtime env file, and returns it in the response."""
    from app import config as config_module

    env_path = tmp_path / ".env"

    # Make the config module use our temp env file for both read and write
    monkeypatch.setattr(main_module, "runtime_env_path", env_path)
    monkeypatch.setattr(config_module, "_resolve_dotenv_path", lambda: env_path)

    client = TestClient(main_module.app, client=("127.0.0.1", 50000))

    response = client.post(
        "/api/settings/runtime",
        json={"draft_background_music_path": "/Users/test/bgm.mp3"},
    )
    assert response.status_code == 200

    # Response includes the field
    data = response.json()
    settings_entries = data["settings"]
    bgm_entry = next(
        (s for s in settings_entries if s["name"] == "DRAFT_BACKGROUND_MUSIC_PATH"),
        None,
    )
    assert bgm_entry is not None
    assert bgm_entry["configured"] is True
    assert bgm_entry["value"] == "/Users/test/bgm.mp3"
    assert bgm_entry["secret"] is False

    # env file was written with the correct key
    assert env_path.exists()
    env_content = env_path.read_text()
    assert "DRAFT_BACKGROUND_MUSIC_PATH=/Users/test/bgm.mp3" in env_content


def test_runtime_settings_bgm_path_rejected_for_non_local_client(tmp_path, monkeypatch):
    """POST /api/settings/runtime rejects non-local client even with BGM path."""
    env_path = tmp_path / ".env"
    monkeypatch.setattr(main_module, "runtime_env_path", env_path)

    remote_client = TestClient(
        main_module.app,
        client=("203.0.113.10", 50000),
    )
    response = remote_client.post(
        "/api/settings/runtime",
        json={"draft_background_music_path": "/tmp/should_not_persist.mp3"},
    )
    assert response.status_code == 403
    assert not env_path.exists()
