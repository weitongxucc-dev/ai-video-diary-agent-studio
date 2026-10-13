from datetime import UTC, datetime

import pytest

from app import env_files, local_runtime_status, runtime_health
from app.config import Settings
from app.model_runtime import ModelCatalog


@pytest.mark.parametrize("available", [False, True])
def test_same_live_ffmpeg_probe_drives_public_health_and_feature_gate(
    tmp_path, monkeypatch, available,
):
    binary_directory = tmp_path / "controlled-bin"
    binary_directory.mkdir()
    monkeypatch.setenv("PATH", str(binary_directory))
    monkeypatch.setenv("AI_VIDEO_DIARY_DOTENV", str(tmp_path / "primary.env"))
    monkeypatch.setattr(env_files, "_repo_root", lambda: tmp_path / "env-repo")
    if available:
        binary = binary_directory / "ffmpeg"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o700)
    settings = Settings(
        obsidian_vault_path=str(tmp_path / "vault"),
        hermes_radar_path=str(tmp_path / "hermes"),
        video_inbox_path=str(tmp_path / "inbox"), video_output_path=str(tmp_path / "output"),
        database_path=str(tmp_path / "studio.sqlite3"),
        model_root_path=str(tmp_path / "models"),
        model_runtime_python_path=str(tmp_path / "python"),
        temporary_root_path=str(tmp_path / "temporary"),
        draft_output_path=str(tmp_path / "drafts"),
        jianying_draft_root_path=str(tmp_path / "jianying"),
    )
    catalog = ModelCatalog(
        settings.model_root_path, settings.model_runtime_python_path,
        tmp_path / "visual.py", tmp_path / "voice.py",
        understanding_runtime_python_path=tmp_path / "understanding-python",
        understanding_worker_path=tmp_path / "understanding.py",
    )
    checked_at = datetime(2026, 10, 14, tzinfo=UTC)
    item = runtime_health._ffmpeg_item(checked_at)
    gate = next(row for row in runtime_health._feature_gates(settings, catalog, [])
                if row["feature"] == "process-video")
    assert settings.public_status()["video"]["ffmpeg_available"] is available
    assert item["status"] == ("ready" if available else "missing")
    assert item["reason"] == ("命令可用。" if available else "未在 PATH 中找到 ffmpeg。")
    assert item["last_checked_at"] == checked_at
    assert gate["available"] is available
    assert gate["reason"] == ("" if available else "ffmpeg 未安装，请运行 brew install ffmpeg")
    assert runtime_health.probe_ffmpeg_available is local_runtime_status.ffmpeg_available


def test_ffmpeg_availability_is_live_without_caching(tmp_path, monkeypatch):
    binary_directory = tmp_path / "controlled-bin"
    binary_directory.mkdir()
    monkeypatch.setenv("PATH", str(binary_directory))
    assert local_runtime_status.ffmpeg_available() is False
    binary = binary_directory / "ffmpeg"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    assert local_runtime_status.ffmpeg_available() is True
    binary.unlink()
    assert local_runtime_status.ffmpeg_available() is False
