from __future__ import annotations

import dataclasses
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
    CompletedSceneSegment, SceneProbeBudget, SceneProbeCheckpoint, SceneSegment,
    SceneWindow, SceneWindowFacts, detect_scene_segments, frame_difference,
    parse_scene_showinfo, run_scene_probe, scene_command, scene_probe_resource_spec,
    scene_probe_segments, scene_unit_key, scene_window_for_key, scene_windows,
    validate_scene_probe_window,
)
from app.production import MaterialSegment, ProductionRepository, _segment_id


def _scene_test_scheduler(repo: ProductionRepository, tmp_path: Path) -> DeviceScheduler:
    host = pytest.importorskip("app.host_resource_probes")
    # Explicit isolated test envelope; these numbers are not production calibration.
    scene_budget = host.SceneWindowBudget(
        allowed_source_codecs=("mpeg4",), max_source_bytes=3 * 1024 * 1024,
        max_source_width_px=160, max_source_height_px=90,
        max_source_frame_rate_fps=120, max_scan_seconds=15,
        max_output_sample_fps=2, max_output_width_px=320, max_output_frames=30,
        max_threads=1, max_timeout_seconds=12,
        max_process_rss_bytes=64 * 1024 * 1024, max_process_threads=8,
        runtime_sample_seconds=0.05,
        additional_memory_bytes=128 * 1024 * 1024, additional_disk_bytes=1,
    )
    resources = host.HostResourceProbe(
        repo.database_path, disk_paths=(tmp_path,),
        budget=host.HostBudget(
            100, 64 * 1024 * 1024, 2**63, 10 * 1024 * 1024,
            2**63, 3 * 1024 * 1024, 0.02,
            scene_window=scene_budget,
        ),
    )
    return DeviceScheduler(
        repo.database_path, tmp_path / "device.lock",
        owners=MaterialHashOwnerVerifier(repo), resources=resources,
        processes=host.HostProcessProbe(),
    )


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
    repo.material_source_files.bind_material_ingestion("one", expected_directory=str(library))

    def make_runtime(current_repo):
        scheduler = _scene_test_scheduler(current_repo, tmp_path)
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
        candidates = repo.scene_records.pending_material_scene_windows()
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
        assert len(repo.scene_records.pending_material_scene_windows()) == 2
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
        assert len(restarted.scene_records.pending_material_scene_windows()) == 2
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
        assert [item["description"] for item in restarted.material_assets.list_unmatched_material_corrections(
            "one", asset.id,
        )] == ["人工未匹配说明"]
        assert restarted.scene_records.pending_material_scene_windows() == []
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


@pytest.mark.parametrize(
    ("error_name", "expected_state", "expected_reason", "asset_status"),
    [
        ("GrantedCommandTimeout", "failed", "scene_timeout", "failed"),
        ("GrantedCommandResourceLimit", "failed", "scene_resource_limit", "failed"),
        ("GrantedCommandResourceUnavailable", "blocked", "resource_pressure", "blocked"),
    ],
)
def test_scene_timeout_or_resource_stop_never_commits_a_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    error_name: str, expected_state: str, expected_reason: str,
    asset_status: str,
) -> None:
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
    repo.material_source_files.bind_material_ingestion("one", expected_directory=str(library))

    scheduler = _scene_test_scheduler(repo, tmp_path)
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

        def stopped(*args, **kwargs):
            if error_name == "GrantedCommandResourceLimit":
                limits = scheduler.resources.scene_runtime_limits(scene_grant)
                raise gated.GrantedCommandResourceLimit(
                    rss=limits.max_process_rss_bytes + 1,
                    threads=limits.max_process_threads, limits=limits,
                )
            raise getattr(gated, error_name)("test stopped child")

        monkeypatch.setattr(gated, "run_granted_command", stopped)
        assert service.run_granted_scene(scene_grant) is None
        assert repo.list_assets("one")[0].analysis_status == asset_status
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM material_analysis_checkpoints "
                "WHERE unit_key LIKE 'scene-window:v1:%'"
            ).fetchone() == (0,)
            assert conn.execute(
                "SELECT state, COALESCE(last_failure_reason, wait_reason) "
                "FROM device_work_requests WHERE id=?",
                (scene_grant.request_id,),
            ).fetchone() == (expected_state, expected_reason)
        scheduler.assert_released(scene_grant)
    finally:
        scheduler.close()


# ---------------------------------------------------------------------------
# WIL-68: bounded scene probe (SceneProbeBudget / frame differencing / resume).
# ---------------------------------------------------------------------------

def _dark_frame() -> bytes:
    return b"\x00" * 16


def _bright_frame() -> bytes:
    return b"\xff" * 16


def test_scene_probe_budget_rejects_invalid_and_reports_wall_clock() -> None:
    with pytest.raises(ValueError, match="cpu_threads"):
        SceneProbeBudget(cpu_threads=0)
    with pytest.raises(ValueError, match="cpu_threads"):
        SceneProbeBudget(cpu_threads=-1)
    with pytest.raises(ValueError):
        SceneProbeBudget(cpu_threads=1.5)
    with pytest.raises(ValueError):
        SceneProbeBudget(max_source_duration_s=0.0)
    with pytest.raises(ValueError):
        SceneProbeBudget(sampling_fps=0.0)
    budget = SceneProbeBudget()
    assert budget.cpu_threads == 1
    assert budget.max_source_duration_s == 3600.0
    assert budget.sampling_fps == 2.0
    assert budget.wall_clock_s == 3630.0


def test_scene_probe_window_rejects_over_duration() -> None:
    budget = SceneProbeBudget(cpu_threads=1, max_source_duration_s=10.0, sampling_fps=2.0)
    validate_scene_probe_window(0.0, 10.0, budget)  # exact boundary is allowed
    with pytest.raises(ValueError, match="超过预算上限"):
        validate_scene_probe_window(0.0, 10.1, budget)
    with pytest.raises(ValueError, match="源时间区间无效"):
        validate_scene_probe_window(5.0, 5.0, budget)
    with pytest.raises(ValueError, match="源时间区间无效"):
        validate_scene_probe_window(5.0, 3.0, budget)


def test_frame_difference_normalized_and_rejects_mismatch() -> None:
    dark = _dark_frame()
    bright = _bright_frame()
    assert frame_difference(dark, bright) == 1.0
    assert frame_difference(dark, dark) == 0.0
    half = b"\x80" * 16
    assert frame_difference(dark, half) == pytest.approx(128 / 255)
    with pytest.raises(ValueError):
        frame_difference(dark, b"")
    with pytest.raises(ValueError):
        frame_difference(dark, b"\x00" * 8)
    with pytest.raises(ValueError):
        frame_difference("not bytes", dark)  # type: ignore[arg-type]


def test_detect_scene_segments_finds_known_cuts() -> None:
    dark = _dark_frame()
    bright = _bright_frame()
    # 6 frames at 2 fps (0.5s apart): cuts between frames 1-2 (t=1.0) and 3-4 (t=2.0).
    frames = [dark, dark, bright, bright, dark, dark]
    segments = detect_scene_segments(frames, window_start_seconds=0.0, sampling_fps=2.0)
    assert segments == (
        SceneSegment(0.0, 1.0, 1.0),
        SceneSegment(1.0, 2.0, 1.0),
        SceneSegment(2.0, 2.5, 1.0),
    )


def test_detect_scene_segments_confidence_reflects_cut_strength() -> None:
    dark = _dark_frame()
    weak = b"\x40" * 16  # ~0.25 difference
    strong = _bright_frame()
    weak_segments = detect_scene_segments(
        [dark, weak, weak], window_start_seconds=0.0, sampling_fps=2.0, threshold=0.25,
    )
    strong_segments = detect_scene_segments(
        [dark, strong, strong], window_start_seconds=0.0, sampling_fps=2.0, threshold=0.25,
    )
    assert strong_segments[0].confidence == 1.0
    assert 0.5 <= weak_segments[0].confidence < strong_segments[0].confidence


def test_detect_scene_segments_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        detect_scene_segments([], window_start_seconds=-1.0, sampling_fps=2.0)
    with pytest.raises(ValueError):
        detect_scene_segments([], window_start_seconds=0.0, sampling_fps=0.0)
    with pytest.raises(ValueError):
        detect_scene_segments([], window_start_seconds=0.0, sampling_fps=2.0, threshold=1.0)
    with pytest.raises(ValueError):
        detect_scene_segments([_dark_frame(), b""], window_start_seconds=0.0, sampling_fps=2.0)
    assert detect_scene_segments([], window_start_seconds=0.0, sampling_fps=2.0) == ()


def test_scene_probe_segments_tile_window_exactly() -> None:
    segments = scene_probe_segments(0.0, 32.4, 14.0)
    assert [segment.index for segment in segments] == [0, 1, 2]
    assert segments[0].start_seconds == 0.0
    assert segments[-1].end_seconds == pytest.approx(32.4)
    for left, right in zip(segments, segments[1:]):
        assert left.end_seconds == right.start_seconds
    total = sum(segment.end_seconds - segment.start_seconds for segment in segments)
    assert total == pytest.approx(32.4)
    # the declared window is never exceeded
    assert max(segment.end_seconds for segment in segments) <= 32.4


def test_detect_scene_segments_stays_within_declared_window() -> None:
    dark = _dark_frame()
    bright = _bright_frame()
    frames = [dark, bright, bright]
    window_start = 10.0
    segments = detect_scene_segments(
        frames, window_start_seconds=window_start, sampling_fps=2.0,
    )
    assert segments
    assert all(seg.start_seconds >= window_start for seg in segments)
    ceiling = window_start + (len(frames) - 1) / 2.0
    assert all(seg.end_seconds <= ceiling for seg in segments)


def test_scene_probe_checkpoint_roundtrip_and_rejects_corrupt() -> None:
    dark = _dark_frame()
    bright = _bright_frame()
    scenes = detect_scene_segments(
        [dark, bright, bright], window_start_seconds=0.0, sampling_fps=2.0,
    )
    checkpoint = SceneProbeCheckpoint(
        0.0, 6.0, 2.0, 2.0, 0.25, (CompletedSceneSegment(0, scenes),),
    )
    encoded = checkpoint.to_json()
    assert SceneProbeCheckpoint.from_json(encoded) == checkpoint
    with pytest.raises(ValueError):
        SceneProbeCheckpoint.from_json(encoded[:-2])
    with pytest.raises(ValueError):
        SceneProbeCheckpoint.from_json('{"bogus": 1}')
    with pytest.raises(ValueError):
        # duplicate segment indices are rejected
        SceneProbeCheckpoint(
            0.0, 6.0, 2.0, 2.0, 0.25,
            (CompletedSceneSegment(0, scenes), CompletedSceneSegment(0, scenes)),
        )


def test_scene_probe_output_has_no_ready_or_available_marking() -> None:
    dark = _dark_frame()
    bright = _bright_frame()
    segments = detect_scene_segments(
        [dark, dark, bright, bright, dark, dark], window_start_seconds=0.0, sampling_fps=2.0,
    )
    checkpoint = SceneProbeCheckpoint(
        0.0, 2.5, 14.0, 2.0, 0.25, (CompletedSceneSegment(0, segments),),
    )
    text = checkpoint.to_json()
    assert "ready" not in text
    assert "available" not in text
    assert [field.name for field in dataclasses.fields(SceneSegment)] == [
        "start_seconds", "end_seconds", "confidence",
    ]
    assert [field.name for field in dataclasses.fields(SceneProbeBudget)] == [
        "cpu_threads", "max_source_duration_s", "sampling_fps",
    ]


def test_scene_probe_resource_spec_matches_sched_registry() -> None:
    budget = SceneProbeBudget(cpu_threads=3, max_source_duration_s=120.0, sampling_fps=4.0)
    spec = scene_probe_resource_spec(budget)
    assert spec == {"cpu_threads": 3, "wall_clock_s": 150.0}
    assert type(spec["cpu_threads"]) is int
    assert isinstance(spec["wall_clock_s"], float)


def test_scene_probe_resume_skips_completed_segments(tmp_path: Path) -> None:
    budget = SceneProbeBudget(cpu_threads=1, max_source_duration_s=3600.0, sampling_fps=2.0)
    window_start, window_end = 0.0, 6.0
    segment_seconds = 2.0
    checkpoint_path = tmp_path / "checkpoint.json"

    def make_frames(segment: object) -> list[bytes]:
        # 2s segment at 2fps -> 4 frames with one interior cut at t=start+1.0.
        del segment
        return [_dark_frame(), _dark_frame(), _bright_frame(), _bright_frame()]

    calls: dict[int, int] = {}

    def crashing_loader(segment: object) -> list[bytes]:
        index = segment.index  # type: ignore[attr-defined]
        calls[index] = calls.get(index, 0) + 1
        if index == 1 and calls[index] == 1:
            raise RuntimeError("simulated crash mid-run")
        return make_frames(segment)

    with pytest.raises(RuntimeError):
        run_scene_probe(
            window_start_seconds=window_start, window_end_seconds=window_end,
            budget=budget, frame_loader=crashing_loader,
            checkpoint_path=checkpoint_path, segment_seconds=segment_seconds,
        )

    # After the crash only segment 0 is checkpointed; segment 1 is not.
    assert checkpoint_path.exists()
    saved = checkpoint_path.read_text(encoding="utf-8")
    checkpoint = SceneProbeCheckpoint.from_json(saved)
    assert checkpoint.has_segment(0)
    assert not checkpoint.has_segment(1)

    def resume_loader(segment: object) -> list[bytes]:
        index = segment.index  # type: ignore[attr-defined]
        calls[index] = calls.get(index, 0) + 1
        return make_frames(segment)

    result = run_scene_probe(
        window_start_seconds=window_start, window_end_seconds=window_end,
        budget=budget, frame_loader=resume_loader,
        checkpoint_path=checkpoint_path, segment_seconds=segment_seconds,
    )

    # Completed segment 0 was skipped on resume; the crashed segment 1 reloaded once.
    assert calls == {0: 1, 1: 2, 2: 1}

    # A clean run from scratch yields identical scenes.
    clean_calls: dict[int, int] = {}

    def clean_loader(segment: object) -> list[bytes]:
        index = segment.index  # type: ignore[attr-defined]
        clean_calls[index] = clean_calls.get(index, 0) + 1
        return make_frames(segment)

    clean = run_scene_probe(
        window_start_seconds=window_start, window_end_seconds=window_end,
        budget=budget, frame_loader=clean_loader,
        checkpoint_path=tmp_path / "clean.json", segment_seconds=segment_seconds,
    )
    assert clean_calls == {0: 1, 1: 1, 2: 1}
    assert result == clean
    assert len(result) == 6  # 3 segments * 2 scenes each


@pytest.fixture
def typed_scene_record_case(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import _media_preview_test_fixtures as media_fixtures
    from test_material_probe import _Processes, _Resources, probe_payload
    from app.material_probe import parse_ffprobe_output

    def forbidden(*_args, **_kwargs):
        raise AssertionError("scene records verification must not execute media, models or drivers")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(media_fixtures, "write_synthetic_video", forbidden)
    monkeypatch.setattr(MaterialIngestionService, "start", forbidden)
    monkeypatch.setattr(MaterialIngestionService, "run_granted_probe", forbidden)
    monkeypatch.setattr(MaterialIngestionService, "run_granted_scene", forbidden)
    monkeypatch.setattr(MaterialHashUnitExecutor, "execute", forbidden)
    # Tool presence is a controlled metadata fixture; execution remains forbidden.
    monkeypatch.setattr(shutil, "which", lambda name: "/isolated-fixture/" + name)
    assert subprocess.Popen is forbidden
    assert media_fixtures.write_synthetic_video is forbidden
    assert MaterialIngestionService.run_granted_scene is forbidden
    library = tmp_path / "library"
    library.mkdir()
    (library / "clip.mp4").write_bytes(b"typed scene checkpoint source")
    repo = ProductionRepository(tmp_path / "records.sqlite3", library)
    repo.material_source_files.bind_material_ingestion("one", expected_directory=str(library))

    def runtime(repository):
        scheduler = DeviceScheduler(repository.database_path, tmp_path / "device.lock",
            owners=MaterialHashOwnerVerifier(repository), resources=_Resources(), processes=_Processes())
        service = MaterialIngestionService(repository, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0)
        return scheduler, service

    scheduler, service = runtime(repo)
    state = SimpleNamespace(repo=repo, scheduler=scheduler, service=service, runtime=runtime)
    try:
        service.discover_and_submit_hashes()
        service.discover_and_submit_hashes()
        hash_grant = scheduler.pick_and_grant()
        assert hash_grant is not None
        assert service.run_granted_hash(hash_grant) is not None
        probe_grant = scheduler.pick_and_grant()
        assert probe_grant is not None
        candidate = repo.probe_records.material_probe_candidate_for_grant(probe_grant)
        payload = json.loads(probe_payload(duration="32.000"))
        for stream in payload["streams"]:
            stream["duration"] = "32.000"
        payload["streams"][0].update(codec_name="mpeg4", width=160, height=90,
            avg_frame_rate="5/1", r_frame_rate="5/1")
        facts = parse_ffprobe_output(json.dumps(payload).encode())
        scheduler.complete_unit(probe_grant, checkpoint_database_path=repo.database_path,
            checkpoint_write=lambda conn, active: repo.probe_records.commit_material_probe(conn, active, candidate, facts))
        state.asset_id = candidate.asset_id
        yield state
    finally:
        state.scheduler.close()


def _complete_typed_scene_window(state):
    grant = state.scheduler.pick_and_grant()
    assert grant is not None and grant.unit_key.startswith("scene-window:v1:")
    candidate = state.repo.scene_records.material_scene_candidate_for_grant(grant)
    cuts = {0.0: (5.0,), 14.0: (20.0,), 28.0: ()}[candidate.window.start_seconds]
    state.scheduler.complete_unit(grant, checkpoint_database_path=state.repo.database_path,
        checkpoint_write=lambda conn, active: state.repo.scene_records.commit_material_scene(
            conn, active, candidate, SceneWindowFacts(candidate.window, cuts)))
    return grant


def test_typed_scene_records_resume_only_missing_windows_and_preserve_manual_changes(typed_scene_record_case):
    from app.materials.scene_records import MaterialSceneRecordStore

    state = typed_scene_record_case
    assert isinstance(state.repo.scene_records, MaterialSceneRecordStore)
    assert len(state.repo.scene_records.pending_material_scene_windows()) == 3
    state.service.submit_pending_scene_windows()
    first = _complete_typed_scene_window(state)
    assert len(state.repo.scene_records.pending_material_scene_windows()) == 2
    asset = state.repo.get_asset("one", state.asset_id)
    assert asset.segments == []
    state.repo.update_asset_tags("one", asset.id, ["人工标签"])
    asset = state.repo.get_asset("one", asset.id)
    asset.review_status = "approved"
    asset.segments = [
        MaterialSegment(_segment_id(asset.id, 0, 5), asset.id, 0, 5, description="人工匹配说明"),
        MaterialSegment(_segment_id(asset.id, 0, 10), asset.id, 0, 10, description="人工未匹配说明"),
    ]
    state.repo._save_asset(asset)
    state.scheduler.close()
    state.repo = ProductionRepository(state.repo.database_path, state.repo.video_library_path)
    state.scheduler, state.service = state.runtime(state.repo)
    pending = state.repo.scene_records.pending_material_scene_windows()
    assert [item.window.start_seconds for item in pending] == [14.0, 28.0]
    assert first.unit_key not in {item.unit_key for item in pending}
    state.service.submit_pending_scene_windows()
    _complete_typed_scene_window(state)
    assert len(state.repo.get_asset("one", asset.id).segments) == 2  # Still the manual draft, not assembled early.
    _complete_typed_scene_window(state)
    result = state.repo.get_asset("one", asset.id)
    assert [(item.start_seconds, item.end_seconds) for item in result.segments] == [(0.0, 5.0), (5.0, 17.0), (17.0, 20.0), (20.0, 32.0)]
    assert result.user_tags == ["人工标签"] and result.review_status == "approved"
    assert result.segments[0].description == "人工匹配说明"
    assert [item["description"] for item in state.repo.material_assets.list_unmatched_material_corrections("one", asset.id)] == ["人工未匹配说明"]
    assert state.repo.scene_records.pending_material_scene_windows() == []
    with sqlite3.connect(state.repo.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM material_analysis_checkpoints WHERE unit_key LIKE 'scene-window:%'").fetchone() == (3,)
        assert conn.execute("SELECT COUNT(*) FROM device_work_requests").fetchone() == (5,)


def test_typed_scene_tail_assembly_sql_refusal_rolls_back_original_permit(typed_scene_record_case, monkeypatch):
    state = typed_scene_record_case
    state.service.submit_pending_scene_windows()
    _complete_typed_scene_window(state)
    _complete_typed_scene_window(state)
    tail = state.scheduler.pick_and_grant()
    assert tail is not None
    candidate = state.repo.scene_records.material_scene_candidate_for_grant(tail)
    assert candidate.window.start_seconds == 28.0
    original_asset = state.repo.get_asset("one", state.asset_id)
    with sqlite3.connect(state.repo.database_path) as conn:
        original_request = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (tail.request_id,)).fetchone()
        original_permit = conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone()
        conn.execute("""CREATE TRIGGER refuse_scene_assembly BEFORE UPDATE ON material_assets
            BEGIN SELECT RAISE(ABORT, 'isolated scene assembly refusal'); END""")
    observed = []
    assemble = state.repo.scene_records._assemble_scene_segments

    def observe_assembly(connection, *args):
        assert connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM material_analysis_checkpoints WHERE unit_key LIKE 'scene-window:%'").fetchone()[0] == 3
        observed.append(connection)
        return assemble(connection, *args)

    monkeypatch.setattr(state.repo.scene_records, "_assemble_scene_segments", observe_assembly)
    with pytest.raises(sqlite3.IntegrityError, match="isolated scene assembly refusal"):
        state.scheduler.complete_unit(tail, checkpoint_database_path=state.repo.database_path,
            checkpoint_write=lambda conn, active: state.repo.scene_records.commit_material_scene(
                conn, active, candidate, SceneWindowFacts(candidate.window, ())))
    assert len(observed) == 1
    assert state.repo.get_asset("one", state.asset_id) == original_asset
    with sqlite3.connect(state.repo.database_path) as conn:
        assert conn.execute("SELECT * FROM device_work_requests WHERE id=?", (tail.request_id,)).fetchone() == original_request
        assert conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone() == original_permit
        assert conn.execute("SELECT COUNT(*) FROM material_analysis_checkpoints WHERE unit_key LIKE 'scene-window:%'").fetchone() == (2,)
        assert conn.execute("SELECT COUNT(*) FROM device_work_requests").fetchone() == (5,)
