from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from app.device_scheduler import DeviceScheduler
from app.material_ingestion import (
    MaterialHashOwnerVerifier, MaterialHashUnitExecutor, MaterialIngestionService,
)
from app.material_scene import (
    SceneWindow, SceneWindowFacts, parse_scene_showinfo, scene_command,
    scene_unit_key, scene_window_for_key, scene_windows,
)
from app.production import MaterialSegment, ProductionRepository, _segment_id


def test_scene_windows_bound_source_time_and_keep_one_second_overlap() -> None:
    windows = scene_windows(32.4)
    assert windows == (
        SceneWindow(0.0, 14.0, 0.0),
        SceneWindow(14.0, 28.0, 13.0),
        SceneWindow(28.0, 32.4, 27.0),
    )
    assert all(window.scan_seconds <= 15 for window in windows)
    assert scene_windows(0.8) == (SceneWindow(0.0, 0.8, 0.0),)
    with pytest.raises(ValueError, match="时长"):
        scene_windows(0)


def test_scene_boundary_at_window_edge_belongs_only_to_later_window() -> None:
    first, second = scene_windows(28)
    first_output = b"[Parsed_showinfo_3] n: 0 pts: 28 pts_time:14\n"
    second_output = (
        b"[Parsed_showinfo_3] n: 0 pts: 2 pts_time:1\n"
        b"[Parsed_showinfo_3] n: 1 pts: 8 pts_time:4\n"
    )
    assert parse_scene_showinfo(first_output, first).boundaries_seconds == ()
    facts = parse_scene_showinfo(second_output, second)
    assert facts.boundaries_seconds == (14.0, 17.0)
    assert SceneWindowFacts.from_checkpoint_json(facts.checkpoint_json()) == facts


def test_scene_stage_key_includes_source_window_and_probe_result() -> None:
    first, second = scene_windows(28)
    sha = "a" * 64
    first_key = scene_unit_key(sha, "sha256:" + "b" * 64, 0, first)
    assert first_key.startswith("scene-window:v1:") and len(first_key.rsplit(":", 1)[-1]) == 64
    assert first_key != scene_unit_key(sha, "sha256:" + "b" * 64, 0, second)
    assert first_key != scene_unit_key(sha, "sha256:" + "c" * 64, 0, first)
    command = scene_command("/usr/bin/ffmpeg", "/source/clip.mp4", 0, second)
    assert command[command.index("-ss") + 1] == "13.000"
    assert command[command.index("-t") + 1] == "15.000"
    assert command[command.index("-threads") + 1] == "1"
    assert "trim=end_frame=30" in command[command.index("-vf") + 1]
    assert scene_window_for_key(28, sha, "sha256:" + "b" * 64, 0, first_key) == first
    assert scene_window_for_key(28, sha, "sha256:" + "c" * 64, 0, first_key) is None


def test_real_scene_windows_resume_from_checkpoint_and_assemble_source_ranges(tmp_path: Path) -> None:
    host = pytest.importorskip("app.host_resource_probes")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg tools are not installed")
    library = tmp_path / "library"
    library.mkdir()
    source = library / "three-scenes.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=red:s=160x90:r=5:d=5",
         "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=5:d=15",
         "-f", "lavfi", "-i", "color=c=green:s=160x90:r=5:d=12",
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "mpeg4", "-g", "250",
         "-pix_fmt", "yuv420p",
         "-y", str(source)],
        check=True, capture_output=True, timeout=20,
    )
    repo = ProductionRepository(tmp_path / "production.sqlite", library)
    repo.bind_material_ingestion("one", expected_directory=str(library))

    class TestResources:
        def admit(self, request):
            return True

        def continue_unit(self, grant):
            return True

        def released(self, grant):
            return True

    def make_runtime(current_repo):
        scheduler = DeviceScheduler(
            current_repo.database_path, tmp_path / "device.lock",
            owners=MaterialHashOwnerVerifier(current_repo),
            resources=TestResources(), processes=host.HostProcessProbe(),
        )
        service = MaterialIngestionService(
            current_repo, execution_mode="scheduled", scheduler=scheduler,
            stable_seconds=0,
        )
        executor = MaterialHashUnitExecutor()
        executor.bind(service)
        return scheduler, service, executor

    scheduler, service, executor = make_runtime(repo)
    try:
        service.discover_and_submit_hashes()
        service.discover_and_submit_hashes()
        hash_grant = scheduler.pick_and_grant()
        assert hash_grant is not None
        executor.execute(hash_grant, scheduler)
        probe_grant = scheduler.pick_and_grant()
        assert probe_grant is not None and probe_grant.unit_key.startswith("probe-meta:v1:")
        executor.execute(probe_grant, scheduler)
        candidates = repo.pending_material_scene_windows()
        assert len(candidates) == 3
        assert [item.window.scan_seconds for item in candidates] == [14, 15, 5]
        with sqlite3.connect(repo.database_path) as conn:
            resources = json.loads(conn.execute(
                "SELECT resources_json FROM device_work_requests WHERE unit_key=?",
                (candidates[1].unit_key,),
            ).fetchone()[0])
        assert resources == {
            "kind": "material_scene_window", "size_bytes": source.stat().st_size,
            "source_codec": "mpeg4", "source_width_px": 160,
            "source_height_px": 90, "source_frame_rate_fps": 5.0,
            "scan_seconds": 15.0, "output_sample_fps": 2,
            "output_max_width_px": 320, "output_max_frames": 30,
            "ffmpeg_threads": 1, "timeout_seconds": 12.0,
        }
        first_grant = scheduler.pick_and_grant()
        assert first_grant is not None and first_grant.unit_key == candidates[0].unit_key
        executor.execute(first_grant, scheduler)
        assert len(repo.pending_material_scene_windows()) == 2
        asset = repo.list_assets("one")[0]
        assert asset.segments == []
        asset.segments = [
            MaterialSegment(
                _segment_id(asset.id, 0, 5), asset.id, 0, 5,
                description="人工匹配说明",
            ),
            MaterialSegment(
                _segment_id(asset.id, 0, 10), asset.id, 0, 10,
                description="人工未匹配说明",
            ),
        ]
        repo._save_asset(asset)
    finally:
        scheduler.close()

    restarted = ProductionRepository(repo.database_path, library)
    scheduler, service, executor = make_runtime(restarted)
    try:
        assert len(restarted.pending_material_scene_windows()) == 2
        assert service.submit_pending_scene_windows()
        for _ in range(2):
            grant = scheduler.pick_and_grant()
            assert grant is not None and grant.unit_key.startswith("scene-window:v1:")
            executor.execute(grant, scheduler)
        asset = restarted.list_assets("one")[0]
        assert asset.analysis_status == "blocked"
        assert [(segment.start_seconds, segment.end_seconds) for segment in asset.segments] == [
            (0.0, 5.0), (5.0, 17.0), (17.0, 20.0), (20.0, 32.0),
        ]
        assert asset.segments[0].description == "人工匹配说明"
        assert [item["description"] for item in restarted.list_unmatched_material_corrections(
            "one", asset.id,
        )] == ["人工未匹配说明"]
        assert restarted.pending_material_scene_windows() == []
        with sqlite3.connect(repo.database_path) as conn:
            rows = conn.execute(
                "SELECT result_json FROM material_analysis_checkpoints "
                "WHERE unit_key LIKE 'scene-window:v1:%' ORDER BY unit_key"
            ).fetchall()
            assert len(rows) == 3
            boundaries = sorted(
                boundary for (value,) in rows
                for boundary in SceneWindowFacts.from_checkpoint_json(value).boundaries_seconds
            )
            assert boundaries == [5.0, 20.0]
    finally:
        scheduler.close()


def test_scene_timeout_never_commits_a_scene_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = pytest.importorskip("app.host_resource_probes")
    gated = pytest.importorskip("app.granted_subprocess")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg tools are not installed")
    library = tmp_path / "library"
    library.mkdir()
    source = library / "clip.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=red:s=64x64:r=5:d=1", "-c:v", "mpeg4",
         "-y", str(source)],
        check=True, capture_output=True, timeout=10,
    )
    repo = ProductionRepository(tmp_path / "production.sqlite", library)
    repo.bind_material_ingestion("one", expected_directory=str(library))

    class TestResources:
        def admit(self, request):
            return True

        def continue_unit(self, grant):
            return True

        def released(self, grant):
            return True

    scheduler = DeviceScheduler(
        repo.database_path, tmp_path / "device.lock",
        owners=MaterialHashOwnerVerifier(repo), resources=TestResources(),
        processes=host.HostProcessProbe(),
    )
    service = MaterialIngestionService(
        repo, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0,
    )
    executor = MaterialHashUnitExecutor()
    executor.bind(service)
    try:
        service.discover_and_submit_hashes()
        service.discover_and_submit_hashes()
        hash_grant = scheduler.pick_and_grant()
        assert hash_grant is not None
        executor.execute(hash_grant, scheduler)
        probe_grant = scheduler.pick_and_grant()
        assert probe_grant is not None
        executor.execute(probe_grant, scheduler)
        scene_grant = scheduler.pick_and_grant()
        assert scene_grant is not None and scene_grant.unit_key.startswith("scene-window:v1:")

        def timeout(*args, **kwargs):
            raise gated.GrantedCommandTimeout("test deadline")

        monkeypatch.setattr(gated, "run_granted_command", timeout)
        assert service.run_granted_scene(scene_grant) is None
        assert repo.list_assets("one")[0].analysis_status == "failed"
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM material_analysis_checkpoints "
                "WHERE unit_key LIKE 'scene-window:v1:%'"
            ).fetchone() == (0,)
            assert conn.execute(
                "SELECT state, last_failure_reason FROM device_work_requests WHERE id=?",
                (scene_grant.request_id,),
            ).fetchone() == ("failed", "scene_timeout")
    finally:
        scheduler.close()
