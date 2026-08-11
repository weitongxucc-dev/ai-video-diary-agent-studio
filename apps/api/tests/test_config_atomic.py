from pathlib import Path

from app import config
from app.config import _ENV_FILE_OVERRIDE_VAR, _resolve_dotenv_path, write_dotenv_values


def test_resolve_dotenv_path_defaults_to_repo_root(monkeypatch):
    """Without the override env var, _resolve_dotenv_path returns the repo-root .env."""
    monkeypatch.delenv(_ENV_FILE_OVERRIDE_VAR, raising=False)
    result = _resolve_dotenv_path()
    assert result.name == ".env"
    # Default path should end with the repo-root .env, not a userData path
    assert "Library" not in str(result)


def test_resolve_dotenv_path_honours_override(monkeypatch, tmp_path):
    """When AI_VIDEO_DIARY_DOTENV is set, it takes precedence."""
    custom = tmp_path / "custom.env"
    monkeypatch.setenv(_ENV_FILE_OVERRIDE_VAR, str(custom))
    result = _resolve_dotenv_path()
    assert result == custom


def test_runtime_env_file_path_uses_override(monkeypatch, tmp_path):
    """runtime_env_file_path delegates to _resolve_dotenv_path."""
    custom = tmp_path / "user-config.env"
    monkeypatch.setenv(_ENV_FILE_OVERRIDE_VAR, str(custom))
    assert config.runtime_env_file_path() == custom


def test_load_dotenv_files_honours_override(monkeypatch, tmp_path):
    """load_dotenv_files() reads from the override path when set."""
    custom = tmp_path / ".env"
    custom.write_text("OVERRIDE_KEY=override-value\n", encoding="utf-8")
    monkeypatch.setenv(_ENV_FILE_OVERRIDE_VAR, str(custom))
    # Clear any pre-existing env value
    monkeypatch.delenv("OVERRIDE_KEY", raising=False)

    import os

    config.load_dotenv_files()
    assert os.environ.get("OVERRIDE_KEY") == "override-value"


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
