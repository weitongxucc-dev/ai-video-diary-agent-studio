from fastapi.testclient import TestClient
import pytest

import app.main as main_module
from app import env_files
from app.runtime_settings import runtime_settings_updates


@pytest.fixture(autouse=True)
def synthetic_env_root(tmp_path, monkeypatch):
    monkeypatch.setattr(env_files, "_repo_root", lambda: tmp_path / "repo")


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


def test_minimax_domestic_default_preserves_explicit_addresses_and_deepseek():
    from app.config import Settings

    assert Settings().effective_llm_base_url == "https://api.minimax.cn/v1"
    assert Settings().effective_llm_model == "MiniMax-M3"
    for address in ("https://api.minimax.io/v1", "https://example.com/custom/v1"):
        assert Settings(llm_provider="minimax", llm_base_url=address).effective_llm_base_url == address
        updates = runtime_settings_updates({"llm_provider": "minimax", "llm_base_url": address})
        assert updates["LLM_BASE_URL"] == address
    assert Settings(llm_provider="deepseek").effective_llm_base_url == "https://api.deepseek.com"
    assert Settings(deepseek_api_key="synthetic-deepseek").effective_llm_provider == "deepseek"


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


@pytest.mark.parametrize("configure_model_root", [False, True])
def test_temp_setup_rejects_unmounted_volume_even_when_parent_directory_exists(monkeypatch, configure_model_root):
    import os
    import tempfile
    from pathlib import Path
    from app.config import Settings, configure_process_temporary_directory

    base = Path("/Volumes/AI-video-offline-test/project")
    root = base / "temporary"
    old_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda path: True if path == base else old_is_dir(path))
    monkeypatch.setattr(Path, "is_mount", lambda path: False)

    def unexpected_write(*args, **kwargs):
        raise AssertionError("Missing external volume must not create local directories")

    monkeypatch.setattr(Path, "mkdir", unexpected_write)
    before_env = os.environ.get("TMPDIR")
    before_tempdir = tempfile.tempdir
    settings = (
        Settings(model_root_path=str(base / "models"), model_root_path_configured=True)
        if configure_model_root else Settings(temporary_root_path=str(root))
    )
    with pytest.raises(FileNotFoundError, match="外接存储未挂载"):
        configure_process_temporary_directory(settings)
    assert os.environ.get("TMPDIR") == before_env
    assert tempfile.tempdir == before_tempdir


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
    env_path = tmp_path / ".env"

    # Bind the actual IO owner to the synthetic file for both read and write.
    monkeypatch.setattr(main_module, "runtime_env_path", env_path)
    monkeypatch.setattr(env_files, "_resolve_dotenv_path", lambda: env_path)

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



def test_conversation_dependencies_do_not_disable_direct_model_features(tmp_path, monkeypatch):
    from app.assistant_pi import pi_runtime_checks
    from app.config import Settings
    from app.runtime_health import runtime_health
    monkeypatch.setenv("AI_VIDEO_DIARY_PI_EXECUTABLE", str(tmp_path / "missing-pi"))
    monkeypatch.setattr("app.assistant_pi.shutil.which", lambda *args, **kwargs: None)
    settings = Settings(llm_api_key="isolated-synthetic-key", llm_api_key_provider="minimax",
                        model_root_path=str(tmp_path / "models"))
    health = runtime_health(settings)
    items = {x["id"]: x for x in health["items"]}
    gates = {x["feature"]: x for x in health["feature_gates"]}
    assert items["pi"]["status"] == "missing"
    assert items["pi_node"]["status"] == "missing"
    assert not gates["assistant-conversation"]["available"]
    assert "密钥" in items["pi"]["reason"] and "不是" in items["pi"]["reason"]
    assert gates["generate-topics"]["available"]
    assert gates["generate-script"]["available"]
    assert "isolated-synthetic-key" not in str(health)
    assert pi_runtime_checks()[0]["fix_command"] == "npm install -g @earendil-works/pi-coding-agent"
