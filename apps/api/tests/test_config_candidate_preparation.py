"""Prospective settings and scratch-path preparation must preserve the live process."""
import os
import tempfile
from pathlib import Path
from types import MappingProxyType

import pytest

from app import config, env_files
from app.config import (
    Settings,
    configure_process_temporary_directory,
    prepare_process_temporary_directory,
    publish_process_temporary_directory,
)


@pytest.fixture(autouse=True)
def synthetic_env_roots(tmp_path, monkeypatch):
    primary = tmp_path / "primary.env"
    secondary_root = tmp_path / "repo"
    secondary = secondary_root / "apps/api/.env"
    secondary.parent.mkdir(parents=True)
    primary.write_text("LLM_MODEL=synthetic-primary\n")
    secondary.write_text("LLM_MODEL=synthetic-secondary\n")
    monkeypatch.setenv("AI_VIDEO_DIARY_DOTENV", str(primary))
    monkeypatch.setattr(env_files, "_repo_root", lambda: secondary_root)
    return primary, secondary


def test_explicit_mapping_ignores_ambient_and_dotenv_without_mutating_live_state(tmp_path, monkeypatch, synthetic_env_roots):
    monkeypatch.setenv("LLM_API_KEY", "ambient-only-secret")
    monkeypatch.setenv("LLM_MODEL", "ambient-only-model")
    monkeypatch.setenv("TOPIC_SOURCE_URLS", "https://ambient.invalid")
    monkeypatch.setenv("MODEL_ROOT_PATH", str(tmp_path / "ambient-models"))
    candidate = MappingProxyType({
        "AI_VIDEO_DIARY_DOTENV": str(tmp_path / "candidate/accepted.env"),
        "LLM_PROVIDER": "deepseek", "LLM_API_KEY": "synthetic-candidate-secret",
        "LLM_MODEL": "synthetic-candidate-model", "TOPIC_SOURCE_URLS": " https://one.invalid , , https://two.invalid ",
        "DATABASE_PATH": str(tmp_path / "candidate/studio.sqlite3"),
        "TEMPORARY_ROOT_PATH": str(tmp_path / "candidate/tmp"),
        "VIDEO_OUTPUT_PATH": str(tmp_path / "candidate/output"),
    })
    before_env, before_cache = dict(os.environ), tempfile.tempdir
    before_observations = dict(config._llm_service_verifications)
    before_files = [path.read_bytes() for path in synthetic_env_roots]
    def forbidden():
        raise AssertionError("Explicit mapping must not load dotenv")
    monkeypatch.setattr(config, "load_dotenv_files", forbidden)
    settings = Settings.from_env(candidate)
    assert settings.llm_model == "synthetic-candidate-model"
    assert settings.llm_api_key == "synthetic-candidate-secret"
    assert settings.topic_source_urls == ("https://one.invalid", "https://two.invalid")
    assert settings.model_root_path == str(tmp_path / "candidate/models")
    assert settings.model_runtime_python_path == str(tmp_path / "candidate/model-runtime/bin/python")
    assert settings.latentsync_root_path == str(tmp_path / "candidate/model-runtimes/latentsync-mlx-candidate")
    assert settings.database_path == candidate["DATABASE_PATH"]
    assert settings.video_output_path == candidate["VIDEO_OUTPUT_PATH"]
    assert dict(os.environ) == before_env and tempfile.tempdir == before_cache
    assert config._llm_service_verifications == before_observations
    assert [path.read_bytes() for path in synthetic_env_roots] == before_files
    assert not (tmp_path / "candidate").exists()


def test_no_argument_dotenv_entry_and_explicit_resolved_environment_parse_identically(tmp_path, monkeypatch, synthetic_env_roots):
    primary, secondary = synthetic_env_roots
    primary.write_text("LLM_MODEL=synthetic-primary\nTOPIC_SOURCE_URLS=https://primary.invalid\n")
    secondary.write_text("LLM_MODEL=synthetic-secondary\n")
    for name, value in {
        "MODEL_ROOT_PATH": str(tmp_path / "models"),
        "DATABASE_PATH": str(tmp_path / "data/studio.sqlite3"),
        "TEMPORARY_ROOT_PATH": str(tmp_path / "temporary"),
        "VIDEO_OUTPUT_PATH": str(tmp_path / "output"),
        "ASR_PROVIDER": "tencentcloud", "LOCAL_WHISPER_BACKEND": "whisper_cpp",
        "TENCENTCLOUD_ASR_MAX_WAIT_SECONDS": "37", "TENCENTCLOUD_ASR_POLL_INTERVAL_SECONDS": "0.25",
        "TENCENTCLOUD_ASR_MAX_LOCAL_AUDIO_BYTES": "12345", "TENCENTCLOUD_SECRET_ID": "synthetic-id",
        "TENCENTCLOUD_SECRET_KEY": "synthetic-key", "LLM_API_KEY": "synthetic-secret",
        "LLM_API_KEY_PROVIDER": "deepseek", "LLM_PROVIDER": "deepseek",
        "SUPABASE_URL": "https://synthetic.invalid", "SUPABASE_PUBLISHABLE_KEY": "synthetic-public-key",
        "AVATAR_MODEL_ID": "synthetic-model-id", "DRAFT_BACKGROUND_MUSIC_PATH": str(tmp_path / "no-audio.wav"),
    }.items():
        monkeypatch.setenv(name, value)
    loaded = Settings.from_env()
    assert loaded.llm_model == "synthetic-secondary"
    assert loaded.tencentcloud_asr_max_wait_seconds == 37
    assert loaded.tencentcloud_asr_poll_interval_seconds == 0.25
    before = dict(os.environ)
    assert Settings.from_env(MappingProxyType(before)) == loaded
    assert dict(os.environ) == before


def test_empty_explicit_mapping_preserves_defaults_without_inheriting_configuration(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "ambient-invalid-provider")
    monkeypatch.setenv("LOCAL_WHISPER_BACKEND", "ambient-invalid-backend")
    monkeypatch.setenv("LLM_API_KEY", "ambient-secret")
    monkeypatch.setenv("TOPIC_SOURCE_URLS", "https://ambient.invalid")
    settings = Settings.from_env({})
    assert settings.asr_provider == "local_whisper"
    assert settings.local_whisper_backend == "openai_whisper"
    assert settings.llm_api_key is None
    assert settings.topic_source_urls == ("https://watcha.cn/",)
    assert settings.tencentcloud_asr_max_wait_seconds == 120
    assert settings.model_root_path_configured is False
    assert settings.model_root_path == str(Path.home() / "Movies/AI视频日记/models")


@pytest.mark.parametrize("invalid", [
    {"ASR_PROVIDER": "invalid"}, {"LOCAL_WHISPER_BACKEND": "invalid"},
    {"TENCENTCLOUD_ASR_MAX_WAIT_SECONDS": "not-an-integer"},
    {"TENCENTCLOUD_ASR_POLL_INTERVAL_SECONDS": "not-a-number"},
])
def test_rejected_candidate_keeps_environment_cache_observations_and_dotenv(invalid, synthetic_env_roots):
    before_env, before_cache = dict(os.environ), tempfile.tempdir
    before_observations = dict(config._llm_service_verifications)
    before_files = [path.read_bytes() for path in synthetic_env_roots]
    with pytest.raises(ValueError):
        Settings.from_env(invalid)
    assert dict(os.environ) == before_env and tempfile.tempdir == before_cache
    assert config._llm_service_verifications == before_observations
    assert [path.read_bytes() for path in synthetic_env_roots] == before_files


def test_prepare_directory_then_publish_changes_process_state_only_at_publication(tmp_path, monkeypatch):
    root = tmp_path / "candidate-temporary"
    settings = Settings(temporary_root_path=str(root))
    before_env, before_cache = dict(os.environ), tempfile.tempdir
    prepared = prepare_process_temporary_directory(settings)
    assert prepared == root.resolve() and root.is_dir()
    assert dict(os.environ) == before_env and tempfile.tempdir == before_cache
    for key in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(key, os.environ.get(key, ""))
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    assert publish_process_temporary_directory(prepared) == prepared
    assert os.environ["TMPDIR"] == str(prepared) and tempfile.tempdir == str(prepared)
    assert configure_process_temporary_directory(settings) == prepared
    if os.name == "nt":
        assert os.environ["TEMP"] == os.environ["TMP"] == str(prepared)
    import subprocess
    import sys

    child_directory = subprocess.check_output(
        [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"], text=True,
    ).strip()
    assert Path(child_directory) == prepared


def test_regular_file_tmp_refuses_during_preparation_before_any_process_publication(tmp_path):
    root = tmp_path / "ordinary-file"
    root.write_text("synthetic existing file")
    before_env, before_cache = dict(os.environ), tempfile.tempdir
    with pytest.raises(FileExistsError):
        prepare_process_temporary_directory(Settings(temporary_root_path=str(root)))
    assert root.read_text() == "synthetic existing file"
    assert dict(os.environ) == before_env and tempfile.tempdir == before_cache


def test_unconfigured_missing_parent_preparation_does_not_initialize_tempfile_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", None)
    before_env = dict(os.environ)
    prepared = prepare_process_temporary_directory(Settings(model_root_path=str(tmp_path / "missing/models")))
    assert prepared is None and tempfile.tempdir is None
    assert dict(os.environ) == before_env
    assert not (tmp_path / "missing").exists()


def test_configured_missing_parent_retains_explicit_path_instead_of_dev_fallback(tmp_path):
    root = tmp_path / "missing/temporary"
    before_env, before_cache = dict(os.environ), tempfile.tempdir
    assert prepare_process_temporary_directory(Settings(temporary_root_path=str(root))) == root.resolve()
    assert not root.exists()
    assert dict(os.environ) == before_env and tempfile.tempdir == before_cache
