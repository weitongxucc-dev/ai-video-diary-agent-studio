from datetime import date
import io
import json
import zipfile

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.models import EpisodeStatus
from app.repository import EpisodeRepository


client = TestClient(main_module.app)


def test_diagnostics_status_redacts_settings_and_lists_recent_errors(tmp_path):
    repo = EpisodeRepository(today_provider=lambda: date(2026, 6, 7))
    episode = repo.today()
    episode.status = EpisodeStatus.FAILED
    episode.video_job_error = "ffmpeg exited with code 1"
    repo.save(episode)
    main_module.repo = repo
    main_module.settings = Settings(
        deepseek_api_key="sk-real-secret",
        product_hunt_token="ph-real-secret",
        tencentcloud_secret_id="tc-id-secret",
        tencentcloud_secret_key="tc-key-secret",
        video_inbox_path=str(tmp_path / "inbox"),
        video_output_path=str(tmp_path / "output"),
        draft_output_path=str(tmp_path / "drafts"),
        database_path=str(tmp_path / "agent.sqlite3"),
    )

    response = client.get("/api/diagnostics/status")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["recent_error_count"] == 1
    assert body["recent_errors"][0]["message"] == "ffmpeg exited with code 1"
    assert "API 启动日志" in {item["label"] for item in body["log_categories"]}
    assert body["settings_snapshot"]["DEEPSEEK_API_KEY"]["value"] == "已配置"
    assert body["settings_snapshot"]["DEEPSEEK_API_KEY"]["redacted"] is True
    serialized = json.dumps(body, ensure_ascii=False)
    assert "sk-real-secret" not in serialized
    assert "ph-real-secret" not in serialized
    assert "tc-id-secret" not in serialized
    assert "tc-key-secret" not in serialized


def test_diagnostics_status_points_to_desktop_worker_logs(tmp_path):
    repo = EpisodeRepository(today_provider=lambda: date(2026, 6, 7))
    repo.today()
    main_module.repo = repo
    main_module.settings = Settings(
        video_inbox_path=str(tmp_path / "inbox"),
        video_output_path=str(tmp_path / "output"),
        draft_output_path=str(tmp_path / "drafts"),
        database_path=str(tmp_path / "agent.sqlite3"),
    )

    response = client.get("/api/diagnostics/status")

    assert response.status_code == 200
    worker_category = next(
        item for item in response.json()["log_categories"] if item["id"] == "worker"
    )
    assert "macOS 客户端阶段接入" not in worker_category["latest_message"]
    assert "worker.log" in worker_category["latest_message"]
    assert "~/Library/Logs/AI视频日记 Agent Studio" in worker_category["latest_message"]


def test_diagnostics_report_zip_contains_safe_structured_files(tmp_path):
    output = tmp_path / "output"
    drafts = tmp_path / "drafts"
    output.mkdir()
    package_dir = drafts / "AI视频日记_20260607"
    package_dir.mkdir(parents=True)
    (output / "edit_plan.json").write_text(
        json.dumps({"segments": [{"id": "seg_1", "reason": "长停顿"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (package_dir / "manifest.json").write_text(
        json.dumps({"package_name": package_dir.name, "video": {"path": "private.mov"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    repo = EpisodeRepository(today_provider=lambda: date(2026, 6, 7))
    episode = repo.today()
    episode.status = EpisodeStatus.DRAFT_READY
    episode.video_edit_plan_path = str(output / "edit_plan.json")
    episode.draft_package_path = str(package_dir)
    episode.video_job_error = "ASR resource exhausted"
    repo.save(episode)
    main_module.repo = repo
    main_module.settings = Settings(
        deepseek_api_key="sk-real-secret",
        obsidian_vault_path=str(tmp_path / "vault"),
        video_output_path=str(output),
        draft_output_path=str(drafts),
        database_path=str(tmp_path / "agent.sqlite3"),
    )

    response = client.get("/api/diagnostics/report")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert {
            "runtime_status.json",
            "settings_snapshot_redacted.json",
            "last_errors.log",
            "episode_manifest.json",
            "draft_manifest.json",
            "edit_plan.json",
        }.issubset(names)
        settings_snapshot = archive.read("settings_snapshot_redacted.json").decode("utf-8")
        last_errors = archive.read("last_errors.log").decode("utf-8")
        assert "sk-real-secret" not in settings_snapshot
        assert "ASR resource exhausted" in last_errors
        assert "private.mov" in archive.read("draft_manifest.json").decode("utf-8")
        assert not any(name.endswith(".mov") or name.endswith(".mp4") for name in names)
