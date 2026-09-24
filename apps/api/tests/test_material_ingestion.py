from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock
import os
import shutil
import subprocess
import time

import pytest

from app.material_ingestion import MaterialIngestionService
from app.production import ProductionRepository


def make_repo(tmp_path: Path, duration_reader=None, scene_reader=None) -> tuple[ProductionRepository, Path]:
    library = tmp_path / "曜核AI IP一体机"
    library.mkdir(exist_ok=True)
    repo = ProductionRepository(
        tmp_path / "production.sqlite",
        library,
        duration_reader=duration_reader or (lambda path: 1.0),
        scene_reader=scene_reader or (lambda path, duration: []),
    )
    return repo, library


def test_unbound_copy_stability_restart_and_changed_content(tmp_path: Path) -> None:
    duration = Mock(return_value=1.0)
    repo, library = make_repo(tmp_path, duration)
    source = library / "clip.mp4"
    source.write_bytes(b"copying")
    service = MaterialIngestionService(repo, stable_seconds=0)
    assert service.scan_once() == []
    assert repo.list_assets("one") == []
    repo.bind_material_ingestion("one")
    assert service.scan_once() == []
    assert repo.list_assets("one")[0].analysis_status == "blocked"
    source.write_bytes(b"finished")
    assert service.scan_once() == []
    analyzed = service.scan_once()
    assert len(analyzed) == 1 and analyzed[0].analysis_status == "ready"
    assert analyzed[0].review_status == "raw"
    assert repo.search_material_segments("one")
    assert duration.call_count == 1
    assert service.scan_once() == []

    restarted = ProductionRepository(tmp_path / "production.sqlite", library, duration_reader=duration, scene_reader=lambda path, seconds: [])
    assert MaterialIngestionService(restarted, stable_seconds=0).scan_once() == []
    assert duration.call_count == 1
    prior_stat = source.stat()
    source.write_bytes(b"changed!")  # Same size, name and timestamp; content digest still changes.
    os.utime(source, ns=(prior_stat.st_atime_ns, prior_stat.st_mtime_ns))
    changed_service = MaterialIngestionService(restarted, stable_seconds=0)
    assert len(changed_service.scan_once()) == 1
    assert duration.call_count == 2
    assert restarted.list_assets("other") == []
    with pytest.raises(ValueError, match="已绑定另一工作区"):
        restarted.bind_material_ingestion("other")


def test_failure_is_isolated_and_retry_is_explicit(tmp_path: Path) -> None:
    def duration(path: Path) -> float:
        if path.name == "bad.mp4":
            raise ValueError("broken")
        return 1.0

    repo, library = make_repo(tmp_path, duration)
    (library / "good.mp4").write_bytes(b"good")
    (library / "bad.mp4").write_bytes(b"bad")
    repo.bind_material_ingestion("one")
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    service.scan_once()
    assets = {asset.file_name: asset for asset in repo.list_assets("one")}
    assert assets["good.mp4"].analysis_status == "ready"
    assert assets["bad.mp4"].analysis_status == "failed"
    assert service.scan_once() == []
    repo.retry_material_ingestion("one", assets["bad.mp4"].id)
    assert service.scan_once()[0].analysis_status == "failed"


def test_copy_waits_for_stable_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    duration = Mock(return_value=1.0)
    repo, library = make_repo(tmp_path, duration)
    source = library / "copy.mp4"
    source.write_bytes(b"part")
    repo.bind_material_ingestion("one")
    service = MaterialIngestionService(repo, stable_seconds=30)
    service.scan_once()
    clock[0] = 1020
    assert service.scan_once() == []
    source.write_bytes(b"complete")
    clock[0] = 1030
    assert service.scan_once() == []
    clock[0] = 1050
    assert service.scan_once() == []
    assert duration.call_count == 0
    clock[0] = 1061
    assert service.scan_once()[0].analysis_status == "ready"


def test_missing_disk_and_symlink_escape(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    (library / "clip.mp4").write_bytes(b"video")
    outside = tmp_path / "private.mp4"
    outside.write_bytes(b"outside")
    (library / "escape.mp4").symlink_to(outside)
    repo.bind_material_ingestion("one")
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    service.scan_once()
    assert [asset.file_name for asset in repo.list_assets("one")] == ["clip.mp4"]
    library.rename(tmp_path / "disconnected")
    assert service.scan_once() == []
    assert "外接盘" in repo.list_assets("one")[0].analysis_message
    (tmp_path / "disconnected").rename(library)
    assert service.scan_once() == []
    assert repo.list_assets("one")[0].analysis_status == "ready"


def test_reanalysis_preserves_unmatched_manual_correction(tmp_path: Path) -> None:
    boundaries = [2.0]
    repo, library = make_repo(tmp_path, duration_reader=lambda path: 4.0, scene_reader=lambda path, duration: boundaries)
    source = library / "clip.mp4"
    source.write_bytes(b"video")
    repo.bind_material_ingestion("one")
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    asset = service.scan_once()[0]
    first = asset.segments[0]
    repo.update_segment("one", asset.id, first.id, description="人工事实", transcript="人工文字", review_status="raw", user_tags=["手工标签"])
    boundaries[:] = [1.5, 2.5]
    repo.analyze_asset("one", asset.id)
    persisted = repo.get_asset("one", asset.id)
    assert all(segment.id != first.id for segment in persisted.segments)
    assert "待核对" in persisted.analysis_message
    assert repo.list_unmatched_material_corrections("one", asset.id)[0]["description"] == "人工事实"


def test_asset_tags_are_atomic_workspace_scoped_and_searchable(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    (library / "one.mp4").write_bytes(b"one")
    (library / "two.mp4").write_bytes(b"two")
    repo.bind_material_ingestion("one")
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    service.scan_once()
    ids = [asset.id for asset in repo.list_assets("one")]
    repo.update_asset_tags("one", ids[0], ["客户现场"])
    assert repo.search_material_segments("one", tags=["客户现场"])
    assert repo.list_assets_page("one", limit=10, offset=0, query="客户现场")[1] == 1
    with pytest.raises(KeyError):
        repo.update_assets_tags_batch("one", [ids[0], "foreign"], add=["新品"], remove=[])
    assert repo.get_asset("one", ids[0]).user_tags == ["客户现场"]
    updated = repo.update_assets_tags_batch("one", ids, add=["新品"], remove=["客户现场"])
    assert all(asset.user_tags == ["新品"] for asset in updated)
    with pytest.raises(KeyError):
        repo.update_asset_tags("other", ids[0], ["越界"])


def test_manual_edit_during_analysis_survives_merge(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    (library / "clip.mp4").write_bytes(b"video")
    asset = repo.scan_library("one")[0]
    repo.analyze_asset("one", asset.id)
    segment_id = repo.get_asset("one", asset.id).segments[0].id

    def simultaneous_edit(path: Path) -> float:
        repo.update_segment("one", asset.id, segment_id, description="人工保留", transcript="", review_status="raw", user_tags=["保留"])
        return 1.0

    repo.duration_reader = simultaneous_edit
    repo.analyze_asset("one", asset.id)
    segment = repo.get_asset("one", asset.id).segments[0]
    assert segment.description == "人工保留"
    assert segment.user_tags == ["保留"]


def test_generated_video_uses_real_probe_and_scene_tools(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg tools unavailable")
    repo, library = make_repo(tmp_path)
    source = library / "generated.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:d=1.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        check=True,
    )
    real_repo = ProductionRepository(tmp_path / "real.sqlite", library)
    real_repo.bind_material_ingestion("one")
    service = MaterialIngestionService(real_repo, stable_seconds=0)
    service.scan_once()
    analyzed = service.scan_once()
    assert len(analyzed) == 1
    assert analyzed[0].analysis_status == "ready"
    assert analyzed[0].duration_seconds == pytest.approx(1.2, abs=0.2)
    assert analyzed[0].segments
