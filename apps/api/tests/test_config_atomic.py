from pathlib import Path

from app import config
from app.config import load_dotenv_files, runtime_env_file_path, write_dotenv_values


def test_write_dotenv_values_replaces_file_atomically(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DEEPSEEK_MODEL=old-model\nUNCHANGED=yes\n",
        encoding="utf-8",
    )
    replacements: list[tuple[Path, Path]] = []
    real_replace = config.os.replace

    def capture_replace(source, target):
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.exists()
        assert source_path.parent == env_path.parent
        assert source_path.name.startswith(".env.")
        assert source_path.read_text(encoding="utf-8") == (
            "DEEPSEEK_MODEL=new-model\n"
            "UNCHANGED=yes\n"
            "DEEPSEEK_API_KEY=secret\n"
        )
        replacements.append((source_path, target_path))
        real_replace(source_path, target_path)

    monkeypatch.setattr(config.os, "replace", capture_replace)

    write_dotenv_values(
        env_path,
        {
            "DEEPSEEK_MODEL": "new-model",
            "DEEPSEEK_API_KEY": "secret",
        },
    )

    assert len(replacements) == 1
    assert replacements[0][1] == env_path
    assert env_path.read_text(encoding="utf-8") == (
        "DEEPSEEK_MODEL=new-model\n"
        "UNCHANGED=yes\n"
        "DEEPSEEK_API_KEY=secret\n"
    )


def test_runtime_env_file_path_can_be_overridden(tmp_path, monkeypatch):
    env_path = tmp_path / "runtime.env"

    monkeypatch.setenv("AI_VIDEO_DIARY_RUNTIME_ENV_PATH", str(env_path))

    assert runtime_env_file_path() == env_path


def test_load_dotenv_files_reads_runtime_env_override(tmp_path, monkeypatch):
    env_path = tmp_path / "runtime.env"
    env_path.write_text(
        "DEEPSEEK_API_KEY=runtime-secret\n"
        "DEEPSEEK_MODEL=deepseek-v4-flash\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("AI_VIDEO_DIARY_RUNTIME_ENV_PATH", str(env_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)

    load_dotenv_files()

    assert config.os.environ["DEEPSEEK_API_KEY"] == "runtime-secret"
    assert config.os.environ["DEEPSEEK_MODEL"] == "deepseek-v4-flash"


def test_runtime_settings_updates_include_seedance_fields():
    from app.runtime_settings import runtime_settings_updates

    updates = runtime_settings_updates(
        {
            "seedance_api_key": "seedance-key",
            "seedance_base_url": "https://seedance.example.com/tasks",
            "seedance_model": "seedance-test",
        }
    )

    assert updates == {
        "SEEDANCE_API_KEY": "seedance-key",
        "SEEDANCE_BASE_URL": "https://seedance.example.com/tasks",
        "SEEDANCE_MODEL": "seedance-test",
    }


def test_runtime_settings_updates_include_cosyvoice_fields():
    from app.runtime_settings import runtime_settings_updates

    updates = runtime_settings_updates(
        {
            "cosyvoice_api_key": "cosy-key",
            "cosyvoice_base_url": "https://cosyvoice.example.com/tts",
            "cosyvoice_model": "cosyvoice-test",
            "cosyvoice_voice_id": "creator",
        }
    )

    assert updates == {
        "COSYVOICE_API_KEY": "cosy-key",
        "COSYVOICE_BASE_URL": "https://cosyvoice.example.com/tts",
        "COSYVOICE_MODEL": "cosyvoice-test",
        "COSYVOICE_VOICE_ID": "creator",
    }


def test_runtime_settings_updates_include_heygem_fields():
    from app.runtime_settings import runtime_settings_updates

    updates = runtime_settings_updates(
        {
            "heygem_api_key": "heygem-key",
            "heygem_base_url": "http://localhost:8383/tasks",
            "heygem_avatar_id": "creator-avatar",
        }
    )

    assert updates == {
        "HEYGEM_API_KEY": "heygem-key",
        "HEYGEM_BASE_URL": "http://localhost:8383/tasks",
        "HEYGEM_AVATAR_ID": "creator-avatar",
    }
