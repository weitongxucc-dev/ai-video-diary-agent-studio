import json
from pathlib import Path

import pytest

from app import config, env_files, local_runtime_status
from app.config import Settings
from app.hermes_radar import HermesRadarSourceClient
from app.schemas import SystemStatusResponse


@pytest.fixture
def local_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "controlled-bin"))
    monkeypatch.setenv("AI_VIDEO_DIARY_DOTENV", str(tmp_path / "primary.env"))
    monkeypatch.setattr(env_files, "_repo_root", lambda: tmp_path / "env-repo")
    return {
        "obsidian_vault_path": str(tmp_path / "vault"),
        "hermes_radar_path": str(tmp_path / "hermes"),
        "video_inbox_path": str(tmp_path / "inbox"),
        "video_output_path": str(tmp_path / "output"),
        "database_path": str(tmp_path / "studio.sqlite3"),
    }


@pytest.mark.parametrize("kind", ["none", "empty", "missing", "directory", "file"])
def test_whisper_probe_reports_only_file_presence(tmp_path, kind):
    path = tmp_path / "configured-whisper-path"
    if kind == "directory":
        path.mkdir()
    elif kind == "file":
        path.write_bytes(b"")
        path.chmod(0o600)
    value = None if kind == "none" else "" if kind == "empty" else str(path)
    assert local_runtime_status.whisper_file_present(value) is (kind == "file")


def test_whisper_probe_expands_the_supplied_path(tmp_path, monkeypatch):
    file = tmp_path / "synthetic-whisper"
    file.write_bytes(b"")
    calls = []
    original_expanduser = Path.expanduser

    def synthetic_expanduser(path):
        calls.append(str(path))
        return file if str(path) == "~/synthetic-whisper" else original_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", synthetic_expanduser)
    assert local_runtime_status.whisper_file_present("~/synthetic-whisper") is True
    assert calls == ["~/synthetic-whisper"]


def test_config_keeps_short_circuit_before_model_probe(local_paths, tmp_path, monkeypatch):
    settings = Settings(
        **local_paths, local_whisper_python_path=str(tmp_path / "missing-runtime"),
        local_whisper_model_path=str(tmp_path / "must-not-probe-model"),
        model_root_path=str(tmp_path / "models"),
        temporary_root_path=str(tmp_path / "temporary"),
    )
    calls = []

    def first_probe_only(path):
        calls.append(path)
        assert path == settings.local_whisper_runtime_path
        return False

    monkeypatch.setattr(config, "whisper_file_present", first_probe_only)
    assert settings.local_whisper_configured is False
    assert calls == [settings.local_whisper_runtime_path]


def test_missing_paths_preserve_the_four_existing_partitions(local_paths):
    status = local_runtime_status.observe_local_runtime_status(**local_paths)
    assert set(status) == {"obsidian", "hermes_radar", "video", "storage"}
    assert status["obsidian"] == {
        "configured": True, "exists": False,
        "path": local_paths["obsidian_vault_path"], "markdown_count": None,
    }
    assert status["video"]["inbox_exists"] is False
    assert status["video"]["output_exists"] is False
    assert status["video"]["pending_count"] == 0
    assert status["video"]["ffmpeg_available"] is False
    assert status["storage"]["database_exists"] is False
    assert status["hermes_radar"]["exists"] is False
    assert status["hermes_radar"]["total_count"] == 0


def test_unconfigured_optional_paths_stay_unconfigured(local_paths):
    local_paths["obsidian_vault_path"] = None
    local_paths["hermes_radar_path"] = None
    status = local_runtime_status.observe_local_runtime_status(**local_paths)
    assert status["obsidian"]["configured"] is False
    assert status["obsidian"]["path"] is None
    assert status["hermes_radar"]["configured"] is False
    assert status["hermes_radar"]["path"] is None


def test_pending_count_uses_only_original_top_level_extensions(local_paths):
    inbox = Path(local_paths["video_inbox_path"])
    inbox.mkdir()
    for name in ("a.mp4", "b.mov", "c.m4v", "d.MP4", "ignored.mkv", "ignored.txt"):
        (inbox / name).write_bytes(b"synthetic file metadata only")
    (inbox / "folder.mp4").mkdir()
    nested = inbox / "nested"
    nested.mkdir()
    (nested / "ignored.mp4").write_bytes(b"synthetic nested file")
    status = local_runtime_status.observe_local_runtime_status(**local_paths)
    assert status["video"]["inbox_exists"] is True
    assert status["video"]["pending_count"] == 4
    file_inbox = inbox / "a.mp4"
    local_paths["video_inbox_path"] = str(file_inbox)
    status = local_runtime_status.observe_local_runtime_status(**local_paths)
    assert status["video"]["inbox_exists"] is False
    assert status["video"]["pending_count"] == 0


def test_obsidian_is_only_statted_and_database_is_not_opened(local_paths, monkeypatch):
    vault = Path(local_paths["obsidian_vault_path"])
    vault.mkdir()
    note = vault / "synthetic-private-note.md"
    note.write_text("synthetic note body must not be read")
    database = Path(local_paths["database_path"])
    database.mkdir()
    original_read_text = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert path != note, "Status cannot read note contents"
        return original_read_text(path, *args, **kwargs)

    def no_recursive_scan(*args, **kwargs):
        raise AssertionError("Status cannot recursively scan the vault")

    monkeypatch.setattr(Path, "read_text", guarded_read)
    monkeypatch.setattr(Path, "rglob", no_recursive_scan)
    status = local_runtime_status.observe_local_runtime_status(**local_paths)
    assert status["obsidian"]["exists"] is True
    assert status["obsidian"]["markdown_count"] is None
    assert status["storage"]["database_exists"] is True
    assert "synthetic note body" not in json.dumps(status)


def test_hermes_consumes_real_status_once_without_exposing_report_body(local_paths, monkeypatch):
    reports = Path(local_paths["hermes_radar_path"])
    reports.mkdir()
    (reports / "2026-10-13.md").write_text("older synthetic report")
    (reports / "2026-10-14.md").write_text(
        "## Product Hunt 今日热门\n\n**1. Synthetic Product**\n"
        "🔗 https://example.invalid/product\n📝 synthetic private report body\n\n"
        "## 观猹 产品库精选\n\n**1. Synthetic Watch**\n"
        "🔗 https://example.invalid/watch\n📝 synthetic watch body\n",
    )
    original_status = HermesRadarSourceClient.status
    calls = []

    def observed_status(client):
        calls.append(client.report_dir)
        return original_status(client)

    monkeypatch.setattr(HermesRadarSourceClient, "status", observed_status)
    status = local_runtime_status.observe_local_runtime_status(**local_paths)
    assert calls == [reports]
    assert status["hermes_radar"] == {
        "configured": True, "exists": True, "path": str(reports),
        "latest_report": "2026-10-14.md", "total_count": 2,
        "product_hunt_count": 1, "watcha_count": 1,
    }
    assert "synthetic private report body" not in json.dumps(status)


@pytest.mark.parametrize("available", [False, True])
def test_public_schema_and_safe_nested_status_use_the_actual_observations(
    local_paths, tmp_path, monkeypatch, available,
):
    binary_directory = tmp_path / "controlled-bin"
    binary_directory.mkdir()
    if available:
        binary = binary_directory / "ffmpeg"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o700)
    secret = "SYNTHETIC_SECRET_NEVER_PROJECTED"
    settings = Settings(
        **local_paths,
        model_root_path=str(tmp_path / "models"),
        model_runtime_python_path=str(tmp_path / "python"),
        temporary_root_path=str(tmp_path / "temporary"),
        draft_output_path=str(tmp_path / "drafts"),
        jianying_draft_root_path=str(tmp_path / "jianying"),
        llm_api_key=secret, llm_api_key_provider="minimax", product_hunt_token=secret,
    )
    public = settings.public_status()
    safe = settings.safe_runtime_settings("synthetic-workspace")
    schema = SystemStatusResponse.model_validate(public)
    observed = local_runtime_status.observe_local_runtime_status(**local_paths)
    assert schema.video.ffmpeg_available is available
    assert safe["status"] == public
    for section in ("obsidian", "hermes_radar", "video", "storage"):
        assert public[section] == observed[section]
    assert secret not in json.dumps(public)
    assert secret not in json.dumps(safe)
    assert config.observe_local_runtime_status is local_runtime_status.observe_local_runtime_status
