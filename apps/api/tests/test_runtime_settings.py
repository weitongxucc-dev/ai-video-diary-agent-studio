from fastapi.testclient import TestClient

import app.config as config_module
import app.main as main_module
from app.config import Settings
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


def test_local_whisper_defaults_are_not_user_specific(tmp_path, monkeypatch):
    python_path = tmp_path / "Documents" / "声迹" / ".venv-whisper" / "bin" / "python"
    model_path = tmp_path / "Documents" / "声迹" / "infra" / "local-whisper-models" / "small.pt"
    python_path.parent.mkdir(parents=True)
    model_path.parent.mkdir(parents=True)
    python_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    model_path.write_text("model", encoding="utf-8")

    monkeypatch.delenv("LOCAL_WHISPER_PYTHON_PATH", raising=False)
    monkeypatch.delenv("LOCAL_WHISPER_MODEL_PATH", raising=False)
    monkeypatch.setattr(config_module, "load_dotenv_files", lambda: None)
    monkeypatch.setattr(config_module.Path, "home", classmethod(lambda cls: tmp_path))

    settings = Settings.from_env()

    assert settings.local_whisper_python_path is None
    assert settings.local_whisper_model_path is None


def test_public_status_rejects_unsafe_local_whisper_path(tmp_path):
    venv_root = tmp_path / ".venv-whisper"
    python_path = venv_root / "bin" / "python"
    model_path = tmp_path / "small.pt"
    python_path.parent.mkdir(parents=True)
    (venv_root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    python_path.write_text("", encoding="utf-8")
    model_path.write_bytes(b"model")

    status = Settings(
        local_whisper_python_path=str(python_path),
        local_whisper_model_path=str(model_path),
    ).public_status()

    assert status["asr"]["local_whisper_configured"] is False
