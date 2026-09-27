from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from app.device_scheduler import DeviceScheduler, ProcessIdentity
from app.material_ingestion import MaterialHashOwnerVerifier, MaterialIngestionService
from app.material_probe import MaterialProbeFacts, MAX_PROBE_OUTPUT_BYTES, parse_ffprobe_output
from app.production import MaterialProbeDependency, ProductionRepository


def probe_payload(*, duration: str = "3.250", video: bool = True) -> bytes:
    streams = [{
        "index": 0, "codec_type": "video", "codec_name": "h264",
        "width": 640, "height": 360, "duration": "3.25",
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
    assert facts.streams[1].sample_rate == 48000
    assert json.loads(facts.checkpoint_json())["duration_seconds"] == 3.25
    assert MaterialProbeFacts.from_checkpoint_json(facts.checkpoint_json()) == facts


def test_probe_parser_falls_back_to_stream_duration_but_rejects_audio_only() -> None:
    assert parse_ffprobe_output(probe_payload(duration="N/A")).duration_seconds == 3.25
    with pytest.raises(ValueError, match="视频流"):
        parse_ffprobe_output(probe_payload(video=False))


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
        assert repo.get_asset("one", asset_id).analysis_status == "failed"
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
