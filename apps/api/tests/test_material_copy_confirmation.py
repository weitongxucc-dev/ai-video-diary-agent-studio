from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.material_ingestion import MaterialIngestionService
from app.production import ProductionRepository


def repository(tmp_path: Path, duration: Mock) -> ProductionRepository:
    library = tmp_path / "materials"
    library.mkdir(exist_ok=True)
    return ProductionRepository(
        tmp_path / "production.sqlite", library,
        duration_reader=duration, scene_reader=lambda path, seconds: [],
    )


def confirm(repo: ProductionRepository, path: Path, source: str = "manual") -> None:
    repo.confirm_material_copy_complete(
        "one", relative_path=str(path.relative_to(repo.video_library_path)),
        expected_revision=repo.material_copy_revision(path.stat()), confirmation_source=source,
    )


def test_external_copy_requires_confirmation_across_rescan_and_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    duration = Mock(return_value=1.0)
    repo = repository(tmp_path, duration)
    path = repo.video_library_path / "clip.mp4"
    path.write_bytes(b"external copy")
    repo.bind_material_ingestion("one", expected_directory=str(repo.video_library_path))
    service = MaterialIngestionService(repo, stable_seconds=0)
    assert service.scan_once() == []
    assert service.scan_once() == []
    assert repo.scan_library("one") == []
    assert repo.list_assets("one") == []
    assert repo.pending_material_copies("one") == [{
        "relative_path": "clip.mp4", "expected_revision": repo.material_copy_revision(path.stat()),
    }]
    confirm(repo, path)
    reopened = repository(tmp_path, duration)
    with sqlite3.connect(reopened.database_path) as conn:
        saved = conn.execute("SELECT confirmed_revision, confirmation_source, copy_confirmed_at FROM material_ingestion_files").fetchone()
    assert json.loads(saved[0]) == repo.material_copy_revision(path.stat())
    assert saved[1] == "manual" and saved[2]
    resumed = MaterialIngestionService(reopened, stable_seconds=10_000)
    assert resumed.scan_once()[0].analysis_status == "ready"
    assert reopened.pending_material_copies("one") == []
    monkeypatch.setattr("app.production.hashlib.file_digest", Mock(side_effect=AssertionError("unchanged file rehashed")))
    assert resumed.scan_once() == []
    assert len(reopened.list_assets("one")) == 1
    assert duration.call_count == 1


def test_changed_revision_invalidates_confirmation_and_blocks_direct_scan_analysis_retry(tmp_path: Path) -> None:
    duration = Mock(return_value=1.0)
    repo = repository(tmp_path, duration)
    path = repo.video_library_path / "clip.mp4"
    path.write_bytes(b"original")
    repo.bind_material_ingestion("one", expected_directory=str(repo.video_library_path))
    confirm(repo, path, "app_import")
    service = MaterialIngestionService(repo)
    asset = service.scan_once()[0]
    before = path.stat()
    old_revision = repo.material_copy_revision(before)
    path.write_bytes(b"modified")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert repo.material_copy_revision(path.stat()) != old_revision
    with pytest.raises(ValueError, match="修订已变化"):
        repo.confirm_material_copy_complete("one", relative_path="clip.mp4", expected_revision=old_revision, confirmation_source="manual")
    assert repo.scan_library("one") == []
    with pytest.raises(ValueError, match="先确认"):
        repo.analyze_asset("one", asset.id)
    with pytest.raises(ValueError, match="先确认"):
        repo.retry_material_ingestion("one", asset.id)
    assert service.scan_once() == []
    assert service.scan_once() == []
    assert repo.get_asset("one", asset.id).analysis_status == "blocked"
    assert duration.call_count == 1
    confirm(repo, path)
    assert service.scan_once()[0].id == asset.id
    assert duration.call_count == 2
    assert len(repo.list_assets("one")) == 1


def test_app_copy_can_confirm_before_discovery_and_restart_reclaims_interrupted_analysis(tmp_path: Path) -> None:
    duration = Mock(return_value=1.0)
    repo = repository(tmp_path, duration)
    path = repo.video_library_path / "app.mp4"
    path.write_bytes(b"fully committed app copy")
    repo.bind_material_ingestion("one", expected_directory=str(repo.video_library_path))
    confirm(repo, path, "app_import")
    claimed = repo.scan_material_ingestion(stable_seconds=10_000)
    assert len(claimed) == 1
    reopened = repository(tmp_path, duration)
    analyzed = MaterialIngestionService(reopened).scan_once()
    assert analyzed[0].id == claimed[0][0]
    assert analyzed[0].analysis_status == "ready"
    assert len(reopened.list_assets("one")) == 1
    with sqlite3.connect(reopened.database_path) as conn:
        assert conn.execute("SELECT confirmation_source FROM material_ingestion_files").fetchone() == ("app_import",)


def test_confirmation_is_not_validation_and_failed_media_retries_same_confirmed_revision(tmp_path: Path) -> None:
    duration = Mock(side_effect=ValueError("invalid media"))
    repo = repository(tmp_path, duration)
    path = repo.video_library_path / "invalid.mp4"
    path.write_bytes(b"not a valid video")
    repo.bind_material_ingestion("one", expected_directory=str(repo.video_library_path))
    revision = repo.material_copy_revision(path.stat())
    with pytest.raises(ValueError, match="未绑定"):
        repo.confirm_material_copy_complete("other", relative_path="invalid.mp4", expected_revision=revision, confirmation_source="manual")
    with pytest.raises(ValueError, match="绑定目录"):
        repo.confirm_material_copy_complete("one", relative_path="../invalid.mp4", expected_revision=revision, confirmation_source="manual")
    confirm(repo, path)
    assert duration.call_count == 0  # A manual declaration does not parse media.
    service = MaterialIngestionService(repo)
    asset = service.scan_once()[0]
    assert asset.analysis_status == "failed"
    assert "格式或内容无效" in asset.analysis_message
    assert service.scan_once() == []
    repo.retry_material_ingestion("one", asset.id)
    duration.side_effect = None
    duration.return_value = 1.0
    assert service.scan_once()[0].analysis_status == "ready"
    assert duration.call_count == 2
