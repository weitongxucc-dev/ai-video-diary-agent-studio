from pathlib import Path

import pytest

from app import config, env_files, runtime_settings
from app.config import _ENV_FILE_OVERRIDE_VAR, _resolve_dotenv_path, write_dotenv_values


@pytest.fixture(autouse=True)
def synthetic_env_root(tmp_path, monkeypatch):
    original_owner_root = env_files._repo_root
    monkeypatch.setattr(env_files, "_repo_root", lambda: tmp_path / "repo")
    yield original_owner_root


def test_config_exports_the_io_owner_and_settings_writer_consumes_it(synthetic_env_root):
    for name in (
        "_ENV_FILE_OVERRIDE_VAR", "SETTINGS_OWNED_ENV_KEYS",
        "_resolve_dotenv_path", "runtime_env_file_path", "load_dotenv_files",
        "write_dotenv_values",
    ):
        assert getattr(config, name) is getattr(env_files, name)
    assert config._repo_root is synthetic_env_root
    assert runtime_settings.write_dotenv_values is env_files.write_dotenv_values


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


def test_catalog_default_root_uses_supplied_mapping_without_reading_dotenv(monkeypatch, tmp_path):
    ambient = tmp_path / "ambient" / "qa.env"
    prospective = tmp_path / "prospective" / "qa.env"
    monkeypatch.setenv(_ENV_FILE_OVERRIDE_VAR, str(ambient))
    candidate_environment = {_ENV_FILE_OVERRIDE_VAR: str(prospective)}
    candidate = config.model_catalog_configuration(candidate_environment)
    assert candidate["model_root_path"] == str(prospective.parent / "models")
    assert config._default_model_root_path() == str(ambient.parent / "models")
    monkeypatch.setenv(_ENV_FILE_OVERRIDE_VAR, str(tmp_path / "different" / "qa.env"))
    assert config.model_catalog_configuration(candidate_environment) == candidate
    assert config.model_catalog_configuration({})["model_root_path"] == str(Path.home() / "Movies" / "AI视频日记" / "models")
    assert not prospective.exists() and not ambient.exists()


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
    real_replace = env_files.os.replace

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

    monkeypatch.setattr(env_files.os, "replace", capture_replace)

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


def test_load_preserves_primary_secondary_and_inherited_value_rules(tmp_path, monkeypatch):
    primary = tmp_path / "primary.env"
    secondary = env_files._repo_root() / "apps/api/.env"
    secondary.parent.mkdir(parents=True)
    primary.write_text("DEEPSEEK_MODEL=primary\nENV_IO_SYNTHETIC=in-primary\n")
    secondary.write_text("DEEPSEEK_MODEL=secondary\nENV_IO_SYNTHETIC=in-secondary\n")
    monkeypatch.setenv(_ENV_FILE_OVERRIDE_VAR, str(primary))
    monkeypatch.setenv("DEEPSEEK_MODEL", "inherited")
    monkeypatch.setenv("ENV_IO_SYNTHETIC", "inherited")
    env_files.load_dotenv_files()
    assert env_files.os.environ["DEEPSEEK_MODEL"] == "secondary"
    assert env_files.os.environ["ENV_IO_SYNTHETIC"] == "inherited"


def test_failed_atomic_replace_keeps_file_environment_and_reload_unchanged(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    original = b"# synthetic configuration\nDEEPSEEK_MODEL=original\n"
    env_path.write_bytes(original)
    monkeypatch.setenv("DEEPSEEK_MODEL", "original")
    reloads = []
    failure = PermissionError("synthetic replacement refused")

    def refuse_replace(source, target):
        assert Path(source).read_text().endswith("DEEPSEEK_MODEL=updated\n")
        assert Path(target) == env_path
        raise failure

    monkeypatch.setattr(env_files.os, "replace", refuse_replace)
    with pytest.raises(PermissionError) as caught:
        runtime_settings.apply_runtime_settings_update(
            env_path, {"deepseek_model": "updated"}, lambda: reloads.append(True),
        )
    assert caught.value is failure
    assert env_path.read_bytes() == original
    assert env_files.os.environ["DEEPSEEK_MODEL"] == "original"
    assert list(tmp_path.glob(".env.*.tmp")) == []
    assert reloads == []
