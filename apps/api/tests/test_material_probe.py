from __future__ import annotations

import json
import hashlib
import os
import shlex
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.device_scheduler import (
    DeviceScheduler, ProcessIdentity, SchedulePolicy, SubmissionIntent, VerifiedWork,
)
from app.material_ingestion import (
    MaterialHashOwnerVerifier, MaterialHashServiceActor, MaterialHashUnitExecutor,
    MaterialIngestionService,
)
from app.material_probe import MaterialProbeFacts, MAX_PROBE_OUTPUT_BYTES, parse_ffprobe_output
from app.production import MaterialProbeDependency, ProductionRepository, material_probe_unit_key


def probe_payload(*, duration: str = "3.250", video: bool = True) -> bytes:
    streams = [{
        "index": 0, "codec_type": "video", "codec_name": "h264",
        "width": 640, "height": 360, "duration": "3.25",
        "avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001",
    }] if video else []
    streams.append({
        "index": 1, "codec_type": "audio", "codec_name": "aac",
        "sample_rate": "48000", "duration": "3.25",
    })
    return json.dumps({
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": duration},
        "streams": streams,
    }).encode()


def test_probe_parser_keeps_typed_media_facts_and_ignores_extra_metadata() -> None:
    facts = parse_ffprobe_output(probe_payload())
    assert facts.duration_seconds == 3.25
    assert facts.format_name.startswith("mov,mp4")
    assert [(item.kind, item.codec) for item in facts.streams] == [
        ("video", "h264"), ("audio", "aac"),
    ]
    assert facts.streams[0].width == 640 and facts.streams[0].height == 360
    assert facts.streams[0].avg_frame_rate_fps == 29.97
    assert facts.streams[0].r_frame_rate_fps == 29.97
    assert facts.streams[1].sample_rate == 48000
    assert json.loads(facts.checkpoint_json())["duration_seconds"] == 3.25
    assert MaterialProbeFacts.from_checkpoint_json(facts.checkpoint_json()) == facts


def test_probe_parser_falls_back_to_stream_duration_but_rejects_audio_only() -> None:
    assert parse_ffprobe_output(probe_payload(duration="N/A")).duration_seconds == 3.25
    with pytest.raises(ValueError, match="视频流"):
        parse_ffprobe_output(probe_payload(video=False))


def test_probe_parser_marks_unknown_or_variable_reported_rate_unavailable() -> None:
    payload = json.loads(probe_payload())
    payload["streams"][0]["avg_frame_rate"] = "0/0"
    payload["streams"][0]["r_frame_rate"] = "5/1"
    stream = parse_ffprobe_output(json.dumps(payload).encode()).streams[0]
    assert stream.avg_frame_rate_fps is None
    assert stream.r_frame_rate_fps == 5.0


def test_probe_parser_rejects_malformed_unbounded_or_invalid_duration() -> None:
    with pytest.raises(ValueError, match="JSON"):
        parse_ffprobe_output(b"{broken")
    with pytest.raises(ValueError, match="上限"):
        parse_ffprobe_output(b" " * (MAX_PROBE_OUTPUT_BYTES + 1))
    with pytest.raises(ValueError, match="时长"):
        payload = json.loads(probe_payload(duration="NaN"))
        payload["streams"][0].pop("duration")
        payload["streams"][1].pop("duration")
        parse_ffprobe_output(json.dumps(payload).encode())


class _Resources:
    def admit(self, request):
        return True

    def continue_unit(self, grant):
        return True

    def released(self, grant):
        return True


class _Processes:
    def current(self):
        return ProcessIdentity(os.getpid(), f"test-{os.getpid()}", "test-boot")

    def children_released(self, children):
        return not children

    def recovery_state(self, holder, children):
        return "released" if not children else "unknown"


class _CheckpointProbeOwner(MaterialHashOwnerVerifier):
    """Test only: allow the metadata checkpoint without launching ffprobe."""

    def check_current(self, request, conn):
        if request.unit_key.startswith("probe-meta:v1:"):
            return self.repository.material_probe_source_verdict(
                request.workspace_id, request.owner_id, request.input_digest,
                request.unit_key, conn,
            )
        return super().check_current(request, conn)


def test_probe_checkpoint_preserves_manual_tags_and_restart_pending_marker(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "clip.mp4"
    source.write_bytes(b"source for checkpoint test")
    repo = ProductionRepository(tmp_path / "production.sqlite", library)
    repo.bind_material_ingestion("one", expected_directory=str(library))
    scheduler = DeviceScheduler(
        repo.database_path, tmp_path / "device.lock", owners=_CheckpointProbeOwner(repo),
        resources=_Resources(), processes=_Processes(),
    )
    service = MaterialIngestionService(
        repo, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0,
    )
    try:
        service.discover_and_submit_hashes()
        service.discover_and_submit_hashes()
        asset_id = repo.list_assets("one")[0].id
        repo.update_asset_tags("one", asset_id, ["人工保留"])
        hash_grant = scheduler.pick_and_grant()
        assert hash_grant is not None
        source_sha = service.run_granted_hash(hash_grant)
        assert isinstance(source_sha, str)
        probe_grant = scheduler.pick_and_grant()
        assert probe_grant is not None and probe_grant.unit_key.startswith("probe-meta:v1:")
        candidate = repo.material_probe_candidate_for_grant(probe_grant)
        facts = parse_ffprobe_output(probe_payload())
        scheduler.complete_unit(
            probe_grant, checkpoint_database_path=repo.database_path,
            checkpoint_write=lambda conn, active: repo.commit_material_probe(
                conn, active, candidate, facts,
            ),
        )
        asset = repo.get_asset("one", asset_id)
        assert asset.duration_seconds == 3.25
        assert asset.user_tags == ["人工保留"]
        assert asset.analysis_status == "blocked" and not asset.segments
        scheduler.close()
        restarted = ProductionRepository(repo.database_path, library)
        assert restarted.pending_material_scene_dependencies() == [
            MaterialProbeDependency("one", asset_id, source_sha, hash_grant.input_digest),
        ]
        with sqlite3.connect(repo.database_path) as conn:
            conn.execute(
                "UPDATE material_analysis_checkpoints SET result_ref='sha256:bad' "
                "WHERE unit_key LIKE 'probe-meta:v1:%'"
            )
        assert restarted.pending_material_scene_dependencies() == []
    finally:
        scheduler.close()


def test_probe_terminal_failure_is_persisted_and_not_auto_retried(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "clip.mp4").write_bytes(b"not a decodable video")
    repo = ProductionRepository(tmp_path / "production.sqlite", library)
    repo.bind_material_ingestion("one", expected_directory=str(library))
    scheduler = DeviceScheduler(
        repo.database_path, tmp_path / "device.lock", owners=_CheckpointProbeOwner(repo),
        resources=_Resources(), processes=_Processes(),
    )
    service = MaterialIngestionService(
        repo, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0,
    )
    try:
        service.discover_and_submit_hashes()
        service.discover_and_submit_hashes()
        asset_id = repo.list_assets("one")[0].id
        hash_grant = scheduler.pick_and_grant()
        assert hash_grant is not None
        assert service.run_granted_hash(hash_grant) is not None
        probe_grant = scheduler.pick_and_grant()
        assert probe_grant is not None
        candidate = repo.material_probe_candidate_for_grant(probe_grant)
        scheduler.fail_unit(
            probe_grant, reason="probe_invalid_media",
            failure_write=lambda conn, active: repo.fail_material_probe(
                conn, active, candidate, "probe_invalid_media",
            ),
        )
        failed_asset = repo.get_asset("one", asset_id)
        assert failed_asset.analysis_status == "failed"
        assert "当前不会自动重试" in failed_asset.analysis_message
        assert "手动重试" not in failed_asset.analysis_message
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute(
                "SELECT state FROM material_ingestion_files"
            ).fetchone() == ("failed",)
            assert conn.execute(
                "SELECT state, last_failure_reason FROM device_work_requests WHERE id=?",
                (probe_grant.request_id,),
            ).fetchone() == ("failed", "probe_invalid_media")
            assert conn.execute(
                "SELECT COUNT(*) FROM material_analysis_checkpoints WHERE unit_key LIKE 'probe-meta:v1:%'"
            ).fetchone() == (0,)
        assert service.discover_and_submit_hashes() == []
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM device_work_requests").fetchone() == (2,)
    finally:
        scheduler.close()


def test_real_short_video_ffprobe_uses_grant_and_leaves_scene_pending(tmp_path: Path) -> None:
    pytest.importorskip("app.granted_subprocess")
    host = pytest.importorskip("app.host_resource_probes")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg tools are not installed")
    library = tmp_path / "library"
    library.mkdir()
    source = library / "short.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=blue:s=64x64:r=5", "-t", "0.6", "-c:v", "mpeg4",
         "-pix_fmt", "yuv420p", "-y", str(source)],
        check=True, capture_output=True, timeout=10,
    )
    repo = ProductionRepository(tmp_path / "production.sqlite", library)
    repo.bind_material_ingestion("one", expected_directory=str(library))
    resources = host.HostResourceProbe(
        repo.database_path, disk_paths=(tmp_path,),
        budget=host.HostBudget(100, 0, 2**63, 0, 2**63, 8 * 1024 * 1024, 0.02),
    )
    scheduler = DeviceScheduler(
        repo.database_path, tmp_path / "device.lock", owners=MaterialHashOwnerVerifier(repo),
        resources=resources, processes=host.HostProcessProbe(),
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
        scheduler.assert_released(hash_grant)
        probe_grant = scheduler.pick_and_grant()
        assert probe_grant is not None and probe_grant.unit_key.startswith("probe-meta:v1:")
        executor.execute(probe_grant, scheduler)
        scheduler.assert_released(probe_grant)
        asset = repo.list_assets("one")[0]
        assert asset.duration_seconds is not None and asset.duration_seconds > 0
        assert asset.analysis_status == "blocked" and not asset.segments
        assert repo.pending_material_scene_dependencies() == [
            MaterialProbeDependency("one", asset.id, probe_grant.input_digest,
                                    hash_grant.input_digest),
        ]
        with sqlite3.connect(repo.database_path) as conn:
            result = conn.execute(
                "SELECT result_json FROM material_analysis_checkpoints "
                "WHERE unit_key LIKE 'probe-meta:v1:%'"
            ).fetchone()
            assert result is not None
            facts = MaterialProbeFacts.from_checkpoint_json(result[0])
            assert facts.streams[0].kind == "video"
            assert conn.execute(
                "SELECT state FROM device_work_requests WHERE id=?", (probe_grant.request_id,)
            ).fetchone() == ("done",)
    finally:
        scheduler.close()


def test_real_invalid_media_ffprobe_failure_is_terminal(tmp_path: Path) -> None:
    pytest.importorskip("app.granted_subprocess")
    host = pytest.importorskip("app.host_resource_probes")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is not installed")
    library = tmp_path / "library"
    library.mkdir()
    (library / "broken.mp4").write_bytes(b"not a video")
    repo = ProductionRepository(tmp_path / "production.sqlite", library)
    repo.bind_material_ingestion("one", expected_directory=str(library))
    resources = host.HostResourceProbe(
        repo.database_path, disk_paths=(tmp_path,),
        budget=host.HostBudget(100, 0, 2**63, 0, 2**63, 8 * 1024 * 1024, 0.02),
    )
    scheduler = DeviceScheduler(
        repo.database_path, tmp_path / "device.lock", owners=MaterialHashOwnerVerifier(repo),
        resources=resources, processes=host.HostProcessProbe(),
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
        scheduler.assert_released(hash_grant)
        probe_grant = scheduler.pick_and_grant()
        assert probe_grant is not None
        executor.execute(probe_grant, scheduler)
        scheduler.assert_released(probe_grant)
        assert repo.list_assets("one")[0].analysis_status == "failed"
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute(
                "SELECT state, last_failure_reason FROM device_work_requests WHERE id=?",
                (probe_grant.request_id,),
            ).fetchone() == ("failed", "probe_failed")
            assert conn.execute(
                "SELECT COUNT(*) FROM material_analysis_checkpoints WHERE unit_key LIKE 'probe-meta:v1:%'"
            ).fetchone() == (0,)
        assert service.discover_and_submit_hashes() == []
    finally:
        scheduler.close()


def test_probe_timeout_cleans_child_and_persists_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("app.granted_subprocess")
    host = pytest.importorskip("app.host_resource_probes")
    fake_probe = tmp_path / "ffprobe"
    fake_probe.write_text("#!/bin/sh\nsleep 5\n")
    fake_probe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr("app.material_ingestion.PROBE_TIMEOUT_SECONDS", 0.2)
    library = tmp_path / "library"
    library.mkdir()
    (library / "clip.mp4").write_bytes(b"source for timeout")
    repo = ProductionRepository(tmp_path / "production.sqlite", library)
    repo.bind_material_ingestion("one", expected_directory=str(library))
    resources = host.HostResourceProbe(
        repo.database_path, disk_paths=(tmp_path,),
        budget=host.HostBudget(100, 0, 2**63, 0, 2**63, 8 * 1024 * 1024, 0.02),
    )
    scheduler = DeviceScheduler(
        repo.database_path, tmp_path / "device.lock", owners=MaterialHashOwnerVerifier(repo),
        resources=resources, processes=host.HostProcessProbe(),
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
        scheduler.assert_released(hash_grant)
        probe_grant = scheduler.pick_and_grant()
        assert probe_grant is not None
        executor.execute(probe_grant, scheduler)
        scheduler.assert_released(probe_grant)
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute(
                "SELECT state, last_failure_reason FROM device_work_requests WHERE id=?",
                (probe_grant.request_id,),
            ).fetchone() == ("failed", "probe_timeout")
        assert repo.list_assets("one")[0].analysis_status == "failed"
    finally:
        scheduler.close()


def test_probe_stop_cancels_registered_child_and_defers_without_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("app.granted_subprocess")
    host = pytest.importorskip("app.host_resource_probes")
    marker = tmp_path / "started"
    fake_probe = tmp_path / "ffprobe"
    fake_probe.write_text(
        "#!/bin/sh\nprintf started > " + shlex.quote(str(marker)) + "\nsleep 5\n"
    )
    fake_probe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    library = tmp_path / "library"
    library.mkdir()
    (library / "clip.mp4").write_bytes(b"source for cancellation")
    repo = ProductionRepository(tmp_path / "production.sqlite", library)
    repo.bind_material_ingestion("one", expected_directory=str(library))
    resources = host.HostResourceProbe(
        repo.database_path, disk_paths=(tmp_path,),
        budget=host.HostBudget(100, 0, 2**63, 0, 2**63, 8 * 1024 * 1024, 0.02),
    )
    scheduler = DeviceScheduler(
        repo.database_path, tmp_path / "device.lock", owners=MaterialHashOwnerVerifier(repo),
        resources=resources, processes=host.HostProcessProbe(),
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
        scheduler.assert_released(hash_grant)
        probe_grant = scheduler.pick_and_grant()
        assert probe_grant is not None
        errors: list[BaseException] = []

        def execute_probe() -> None:
            try:
                executor.execute(probe_grant, scheduler)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=execute_probe)
        worker.start()
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), "the test command must start under its registered grant"
        service.stop()
        worker.join(timeout=3)
        assert not worker.is_alive() and not errors
        scheduler.assert_released(probe_grant)
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute(
                "SELECT state, wait_reason FROM device_work_requests WHERE id=?",
                (probe_grant.request_id,),
            ).fetchone() == ("blocked", "worker_interrupted")
            assert conn.execute(
                "SELECT COUNT(*) FROM material_analysis_checkpoints WHERE unit_key LIKE 'probe-meta:v1:%'"
            ).fetchone() == (0,)
    finally:
        scheduler.close()


@pytest.mark.parametrize(
    ("change", "wait_reason"),
    [("removed", "dependency_unavailable"), ("rewritten", "input_stale")],
)
def test_probe_source_change_before_exec_defers_without_checkpoint(
    tmp_path: Path, change: str, wait_reason: str,
) -> None:
    pytest.importorskip("app.granted_subprocess")
    library = tmp_path / "library"
    library.mkdir()
    source = library / "clip.mp4"
    source.write_bytes(b"first version")
    repo = ProductionRepository(tmp_path / "production.sqlite", library)
    repo.bind_material_ingestion("one", expected_directory=str(library))
    scheduler = DeviceScheduler(
        repo.database_path, tmp_path / "device.lock", owners=MaterialHashOwnerVerifier(repo),
        resources=_Resources(), processes=_Processes(),
    )
    service = MaterialIngestionService(
        repo, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0,
    )
    try:
        service.discover_and_submit_hashes()
        service.discover_and_submit_hashes()
        hash_grant = scheduler.pick_and_grant()
        assert hash_grant is not None
        assert service.run_granted_hash(hash_grant) is not None
        probe_grant = scheduler.pick_and_grant()
        assert probe_grant is not None
        if change == "removed":
            library.rename(tmp_path / "disconnected")
        else:
            source.write_bytes(b"second version is different")
        assert service.run_granted_probe(probe_grant) is None
        scheduler.assert_released(probe_grant)
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute(
                "SELECT state, wait_reason FROM device_work_requests WHERE id=?",
                (probe_grant.request_id,),
            ).fetchone() == ("blocked", wait_reason)
            assert conn.execute(
                "SELECT COUNT(*) FROM material_analysis_checkpoints WHERE unit_key LIKE 'probe-meta:v1:%'"
            ).fetchone() == (0,)
    finally:
        scheduler.close()


@pytest.mark.parametrize("blocked", [False, True])
@pytest.mark.parametrize("new_exists", [False, True])
def test_legacy_probe_request_replaced_once_without_losing_wait_age(
    tmp_path: Path, blocked: bool, new_exists: bool,
) -> None:
    if not hasattr(DeviceScheduler, "replace_unexecuted_request"):
        pytest.skip("SCHED replacement contract not yet integrated")
    pytest.importorskip("app.granted_subprocess")
    library = tmp_path / "library"
    library.mkdir()
    (library / "clip.mp4").write_bytes(b"source for legacy queue")
    repo = ProductionRepository(tmp_path / "production.sqlite", library)
    repo.bind_material_ingestion("one", expected_directory=str(library))
    scheduler = DeviceScheduler(
        repo.database_path, tmp_path / "device.lock", owners=MaterialHashOwnerVerifier(repo),
        resources=_Resources(), processes=_Processes(),
    )
    service = MaterialIngestionService(
        repo, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0,
    )
    try:
        service.discover_and_submit_hashes()
        service.discover_and_submit_hashes()
        hash_grant = scheduler.pick_and_grant()
        assert hash_grant is not None
        result = repo.hash_granted_material(hash_grant, scheduler.assert_grant)
        scheduler.complete_unit(
            hash_grant, checkpoint_database_path=repo.database_path,
            checkpoint_write=lambda conn, active: repo.commit_material_hash(
                conn, active, result,
            ),
        )
        asset_id = repo.list_assets("one")[0].id
        old_stage = hashlib.sha256(json.dumps({
            "stage": "probe-scene:v1", "source_sha256": result.source_sha256,
            "probe_contract": "bounded-window:v1",
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        old_key = f"probe-scene:v1:{old_stage}"

        class LegacyOwner(MaterialHashOwnerVerifier):
            def verify_submission(self, intent, actor):
                if intent.unit_key == old_key:
                    return VerifiedWork(
                        workspace_id="one", owner_type="material_asset", owner_id=asset_id,
                        input_digest=result.source_sha256, unit_key=old_key,
                        member_id=None, work_class="background",
                        resources={"kind": "material_probe_scene", "size_bytes": 23},
                        stage_input_digest=old_stage,
                    )
                return super().verify_submission(intent, actor)

            def check_current(self, request, conn):
                return "dependency" if request.unit_key == old_key else super().check_current(request, conn)

        scheduler.owners = LegacyOwner(repo)
        old = scheduler.submit(
            SubmissionIntent("one", "material_asset", asset_id, result.source_sha256,
                             old_key, SchedulePolicy()),
            MaterialHashServiceActor("one", str(library.resolve())),
        )
        if blocked:
            assert scheduler.pick_and_grant() is None
        scheduler.owners = MaterialHashOwnerVerifier(repo)
        prior_new_id = None
        if new_exists:
            prior_new_id = scheduler.submit(
                SubmissionIntent(
                    "one", "material_asset", asset_id, result.source_sha256,
                    material_probe_unit_key(result.source_sha256), SchedulePolicy(),
                ),
                MaterialHashServiceActor("one", str(library.resolve())),
            ).id
        with sqlite3.connect(repo.database_path) as conn:
            before = conn.execute(
                "SELECT first_ready_at_utc, eligible_wait_seconds FROM device_work_requests WHERE id=?",
                (old.id,),
            ).fetchone()
        replacement = service.submit_pending_probe_dependencies()
        assert len(replacement) == 1
        if prior_new_id is not None:
            assert replacement == [prior_new_id]
        assert service.submit_pending_probe_dependencies() == replacement
        with sqlite3.connect(repo.database_path) as conn:
            old_state = conn.execute(
                "SELECT state, wait_reason FROM device_work_requests WHERE id=?", (old.id,)
            ).fetchone()
            new = conn.execute(
                "SELECT first_ready_at_utc, eligible_wait_seconds FROM device_work_requests WHERE id=?",
                (replacement[0],),
            ).fetchone()
            assert old_state == ("cancelled", f"replaced_by:{replacement[0]}")
            assert new == before
            assert conn.execute("SELECT COUNT(*) FROM device_work_requests").fetchone() == (3,)
            conn.execute(
                "UPDATE device_work_requests SET state='done' WHERE id=?", (old.id,)
            )
        with pytest.raises(ValueError, match="拒绝自动替换"):
            service.submit_pending_probe_dependencies()
    finally:
        scheduler.close()
