"""External-volume workspace relocation: verified copy, rebind and fail-closed gates."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import volume_identity
from app import workspace_relocate
from app.materials.analysis_runs import MaterialAnalysisRunStore
from app.materials.contracts import MaterialAsset, MaterialSegment
from app.models import utc_now
from app.persistence_json import _json_payload
from app.production import ProductionRepository
from app.volume_identity import VolumeIdentity
from app.workspace_relocate import (
    RelocationError,
    RelocationRequest,
    _copy_file,
    _request_from_args,
    _verify_database_copy,
    plan_relocation,
    run_relocation,
)

WORKSPACE = "local"
IDENTITY = VolumeIdentity(
    mount_point="/Volumes/test-volume", uuid="volume-uuid-198",
    volume_name="test-volume", filesystem="apfs",
)


def _revision(stat: os.stat_result) -> dict[str, str]:
    return {
        "size_bytes": str(stat.st_size), "mtime_ns": str(stat.st_mtime_ns),
        "ctime_ns": str(stat.st_ctime_ns), "inode": str(stat.st_ino),
    }


def _execute(database: Path, statement: str, parameters: tuple = ()) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(statement, parameters)
        connection.commit()
    finally:
        connection.close()


def _rows(database: Path, statement: str, parameters: tuple = ()) -> list[sqlite3.Row]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(statement, parameters).fetchall()
    finally:
        connection.close()


def _row(database: Path, statement: str, parameters: tuple = ()) -> sqlite3.Row | None:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(statement, parameters).fetchone()
    finally:
        connection.close()


@dataclass
class Workspace:
    internal: Path
    source: dict[str, Path]
    external: Path
    target: dict[str, Path]
    env_path: Path
    receipt_path: Path
    record_path: Path
    lock_path: Path

    @property
    def media(self) -> Path:
        return self.source["materials"] / "clips" / "a.mp4"

    @property
    def target_media(self) -> Path:
        return self.target["materials"] / "clips" / "a.mp4"

    def request(self, **overrides) -> RelocationRequest:
        values = dict(
            source=self.source, target=self.target, env_path=self.env_path,
            receipt_path=self.receipt_path, record_path=self.record_path,
            lock_path=self.lock_path,
            is_external=lambda _path: True,
            probe=lambda _path: IDENTITY,
            free_bytes=lambda _path: 10**12,
        )
        values.update(overrides)
        return RelocationRequest(**values)


def _build_workspace(tmp_path: Path, monkeypatch) -> Workspace:
    internal = tmp_path / "internal"
    library = internal / "01_待处理视频"
    media = library / "clips" / "a.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"video-bytes-198")
    outputs = internal / "06_待发布成片"
    outputs.mkdir()
    (outputs / "render.mp4").write_bytes(b"render-bytes-198")
    drafts = internal / "05_剪映草稿"
    drafts.mkdir()
    (drafts / "draft.json").write_text('{"draft": 198}')
    temporary = internal / "temporary"
    temporary.mkdir()
    (temporary / "cache.bin").write_bytes(b"temporary-bytes-198")
    database = internal / "agent.sqlite3"

    ProductionRepository(database, library)
    stat = media.stat()
    revision = _revision(stat)
    revision_json = json.dumps(revision, sort_keys=True)
    now = utc_now()
    asset = MaterialAsset(
        id="asset-198", workspace_id=WORKSPACE, file_name=media.name,
        path=str(media.resolve()), size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        duration_seconds=1.0,
        segments=[MaterialSegment(
            id="seg-198", asset_id="asset-198", start_seconds=0.0, end_seconds=1.0,
            source_revision=revision, analysis_version="analysis-198",
        )],
        created_at=now, updated_at=now,
    )
    _execute(database, "INSERT INTO material_ingestion_binding(id,workspace_id,library_path) VALUES(1,?,?)",
             (WORKSPACE, str(library.resolve())))
    _execute(database, "INSERT INTO material_assets(id,workspace_id,file_path,payload,updated_at) VALUES(?,?,?,?,?)",
             (asset.id, WORKSPACE, asset.path, _json_payload(asset), now.isoformat()))
    _execute(
        database,
        "INSERT INTO material_ingestion_files(workspace_id,file_path,size_bytes,mtime_ns,ctime_ns,"
        "inode,observed_at,state,confirmed_revision) VALUES(?,?,?,?,?,?,?,?,?)",
        (WORKSPACE, asset.path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
         stat.st_ino, 0.0, "ready", revision_json),
    )
    _execute(
        database,
        "INSERT INTO material_evidence_refs(id,workspace_id,asset_id,segment_id,source_ref,"
        "source_revision_json,source_time_range_json,locator_type,locator_json,modality,"
        "model_or_rule,analysis_version,sampling_coverage_json,status,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("evi-198", WORKSPACE, asset.id, "seg-198", asset.path, revision_json,
         "[0.0,1.0]", "source_time_range",
         json.dumps({"segment_id": "seg-198", "path": asset.path}, sort_keys=True),
         "segment_observation", "ffmpeg", "analysis-198", "{}", "valid", now.isoformat()),
    )
    _execute(
        database,
        "INSERT INTO material_index_records(id,workspace_id,asset_id,index_type,"
        "source_revision_json,source_digest,model_or_rule,index_version,coverage_json,"
        "status,invalid_reason,built_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("idx-198", WORKSPACE, asset.id, "material_search", revision_json, "digest-198",
         "material_payload_v1", "v1", "{}", "ready", None, now.isoformat(), now.isoformat()),
    )
    _execute(
        database,
        "INSERT INTO material_quality_profiles(id,workspace_id,asset_id,source_revision_json,"
        "source_digest,analysis_run_id,profile_version,status,metrics_json,"
        "unavailable_reasons_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("quality-198", WORKSPACE, asset.id, revision_json, "digest-198", "analysis-198",
         "quality-v1", "unavailable", "{}", "[]", now.isoformat(), now.isoformat()),
    )
    _execute(
        database,
        "INSERT INTO material_segment_lineage(workspace_id,asset_id,parent_segment_id,"
        "child_segment_id,relation,source_revision_json,created_at) VALUES(?,?,?,?,?,?,?)",
        (WORKSPACE, asset.id, "seg-old", "seg-198", "preserved", revision_json, now.isoformat()),
    )
    run_store = MaterialAnalysisRunStore(lambda: _connect(database))
    run = run_store.start(WORKSPACE, asset.id, source_revision=revision, source_digest="digest-198")
    run_store.finish(WORKSPACE, run.id, status="succeeded")
    _execute(
        database,
        "INSERT INTO reference_video_studies(id,workspace_id,asset_id,source_revision_json,"
        "source_digest,analysis_run_id,status,payload,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("study-198", WORKSPACE, asset.id, revision_json, "digest-198", "analysis-198",
         "ready", json.dumps({"id": "study-198", "source_revision": revision}, sort_keys=True),
         now.isoformat(), now.isoformat()),
    )
    _execute(
        database,
        "INSERT INTO creation_plans(id,workspace_id,payload,updated_at) VALUES(?,?,?,?)",
        ("plan-198", WORKSPACE, json.dumps({
            "source_media": {"asset_id": asset.id, "file_signature": revision},
        }), now.isoformat()),
    )

    source = {
        "database": database,
        "materials": library,
        "outputs": outputs,
        "drafts": drafts,
        "temporary": temporary,
    }
    external = tmp_path / "external"
    external.mkdir()
    target = {name: external / path.name for name, path in source.items()}
    env_path = tmp_path / "config" / ".env"
    env_path.parent.mkdir()
    env_path.write_text("".join(
        f"{spec}={path}\n"
        for spec, path in {
            "DATABASE_PATH": database, "VIDEO_INBOX_PATH": library,
            "VIDEO_OUTPUT_PATH": outputs, "DRAFT_OUTPUT_PATH": drafts,
            "TEMPORARY_ROOT_PATH": temporary,
        }.items()
    ), encoding="utf-8")
    for name, value in {
        "DATABASE_PATH": database, "VIDEO_INBOX_PATH": library,
        "VIDEO_OUTPUT_PATH": outputs, "DRAFT_OUTPUT_PATH": drafts,
        "TEMPORARY_ROOT_PATH": temporary,
    }.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setattr(volume_identity, "volume_root", lambda _path: Path("/Volumes/test-volume"))
    monkeypatch.setattr(Path, "is_mount", lambda _self: True)
    return Workspace(
        internal=internal, source=source, external=external, target=target,
        env_path=env_path, receipt_path=tmp_path / "relocation.json",
        record_path=tmp_path / "volume-identity.json", lock_path=tmp_path / "device.lock",
    )


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def test_relocation_copies_verifies_rebinds_and_updates_configuration(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    receipt = run_relocation(workspace.request())

    assert receipt["status"] == "applied"
    assert receipt["rebind"]["status"] == "applied"
    assert receipt["rebind"]["workspace_id"] == WORKSPACE
    # Source data is retained, never deleted or rebound.
    assert workspace.media.read_bytes() == b"video-bytes-198"
    assert _row(workspace.source["database"],
                "SELECT library_path FROM material_ingestion_binding WHERE id=1")[0] == str(
                    workspace.source["materials"].resolve())
    # Every media root was copied with identical bytes and preserved mtime. The
    # database target is the verified backup snapshot, not a byte copy.
    for name, source in workspace.source.items():
        target = workspace.target[name]
        if name == "database":
            with closing(sqlite3.connect(target)) as connection:
                assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            continue
        assert target.is_dir()
        for item in source.rglob("*"):
            if item.is_file():
                copy = target / item.relative_to(source)
                assert copy.read_bytes() == item.read_bytes()
                assert copy.stat().st_mtime_ns == item.stat().st_mtime_ns
    # Configuration now points at the target, and the source values are gone.
    configured = dict(
        line.split("=", 1) for line in workspace.env_path.read_text().splitlines()
    )
    assert configured["DATABASE_PATH"] == str(workspace.target["database"])
    assert configured["VIDEO_INBOX_PATH"] == str(workspace.target["materials"])
    assert configured["TEMPORARY_ROOT_PATH"] == str(workspace.target["temporary"])
    assert os.environ["VIDEO_OUTPUT_PATH"] == str(workspace.target["outputs"])
    # Volume identity is recorded beside the dotenv file.
    record = json.loads(workspace.record_path.read_text())
    assert record["volumes"][IDENTITY.mount_point]["uuid"] == IDENTITY.uuid

    database = workspace.target["database"]
    new_revision = _revision(workspace.target_media.stat())
    binding = _row(database, "SELECT library_path FROM material_ingestion_binding WHERE id=1")
    assert binding[0] == str(workspace.target["materials"].resolve())
    asset = _row(database, "SELECT file_path,payload FROM material_assets WHERE id='asset-198'")
    assert asset["file_path"] == str(workspace.target_media.resolve())
    payload = json.loads(asset["payload"])
    assert payload["path"] == str(workspace.target_media.resolve())
    assert payload["segments"][0]["source_revision"] == new_revision
    assert _row(database, "SELECT file_path,confirmed_revision FROM material_ingestion_files")[0] == asset["file_path"]
    evidence = _row(database, "SELECT source_ref,locator_json,source_revision_json FROM material_evidence_refs")
    assert evidence["source_ref"] == str(workspace.target_media.resolve())
    assert json.loads(evidence["locator_json"])["path"] == str(workspace.target_media.resolve())
    assert json.loads(evidence["source_revision_json"]) == new_revision
    for table in ("material_index_records", "material_quality_profiles",
                  "material_segment_lineage", "reference_video_studies"):
        stored = _row(database, f"SELECT source_revision_json FROM {table}")
        assert json.loads(stored[0]) == new_revision, table
    analysis = _row(database, "SELECT source_revision,payload FROM material_analysis_runs")
    assert json.loads(analysis["source_revision"]) == new_revision
    assert json.loads(analysis["payload"])["source_revision"] == new_revision
    plan = json.loads(_row(database, "SELECT payload FROM creation_plans WHERE id='plan-198'")[0])
    assert plan["source_media"]["file_signature"] == new_revision


def test_relocation_never_rewrites_historical_execution_records(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    database = workspace.source["database"]
    now = utc_now().isoformat()
    resources = json.dumps({"input_path": str(workspace.media.resolve())}, sort_keys=True)
    _execute(
        database,
        "INSERT INTO device_work_requests(id,workspace_id,owner_type,owner_id,input_digest,"
        "unit_key,source_refs_json,work_class,policy,first_ready_at_utc,state,"
        "resources_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("request-198", WORKSPACE, "task", "task-198", "digest-198", "unit-198",
         "[]", "normal_make", "asap", now, "done", resources, now, now),
    )
    _execute(
        database,
        "INSERT INTO job_runs(id,workspace_id,task_id,kind,idempotency_key,input_digest,"
        "status,progress,attempt,output_refs_json,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("job-198", WORKSPACE, "task-198", "render", "idem-198", "digest",
         "succeeded", 100, 0, json.dumps([{"path": str(workspace.media.resolve())}]),
         now, now),
    )
    snapshot = json.dumps({"clips": [{"path": str(workspace.media.resolve())}]}, sort_keys=True)
    _execute(
        database,
        "INSERT INTO timeline_versions(id,workspace_id,task_id,sequence,snapshot_json,created_at) "
        "VALUES(?,?,?,?,?,?)",
        ("version-198", WORKSPACE, "task-198", 1, snapshot, now),
    )

    run_relocation(workspace.request())

    target = workspace.target["database"]
    assert _row(target, "SELECT resources_json FROM device_work_requests")[0] == resources
    assert _row(target, "SELECT output_refs_json FROM job_runs")[0] == json.dumps(
        [{"path": str(workspace.media.resolve())}])
    assert _row(target, "SELECT snapshot_json FROM timeline_versions")[0] == snapshot


def test_cli_reports_the_applied_receipt_after_configuration_has_moved(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    run_relocation(workspace.request())
    monkeypatch.setenv("AI_VIDEO_DIARY_DOTENV", str(workspace.env_path))
    monkeypatch.setattr(workspace_relocate, "_is_external_mount", lambda _path: True)
    monkeypatch.setattr(workspace_relocate, "_free_bytes", lambda _path: 10**12)
    monkeypatch.setattr(volume_identity, "probe_volume_identity", lambda _path: IDENTITY)

    request = _request_from_args(argparse.Namespace(
        target_root=str(workspace.external), database_target=None,
        materials_target=None, outputs_target=None, drafts_target=None,
        temporary_target=None, operation_id=None, receipt=str(workspace.receipt_path),
        volume_record=str(workspace.record_path),
    ))
    receipt = run_relocation(replace(request, lock_path=workspace.lock_path))

    assert request.source["materials"] == workspace.source["materials"]
    assert receipt["status"] == "applied"
    assert receipt["reapplied"] is True


def test_relocation_is_idempotent_and_reports_the_applied_receipt(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    run_relocation(workspace.request())
    target_mtime = workspace.target_media.stat().st_mtime_ns

    receipt = run_relocation(workspace.request())

    assert receipt["status"] == "applied"
    assert receipt["reapplied"] is True
    assert workspace.target_media.stat().st_mtime_ns == target_mtime


def test_relocation_refuses_while_the_application_lock_is_held(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    descriptor = os.open(workspace.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RelocationError, match="应用仍在运行"):
            run_relocation(workspace.request())
    finally:
        os.close(descriptor)


def test_relocation_refuses_a_target_that_is_not_a_mounted_external_volume(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    with pytest.raises(RelocationError, match="不是已挂载的外接盘"):
        plan_relocation(workspace.request(is_external=lambda _path: False))


def test_relocation_refuses_source_and_target_overlap(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    target = dict(workspace.target)
    target["materials"] = workspace.source["materials"] / "nested"
    with pytest.raises(RelocationError, match="不能重叠"):
        plan_relocation(workspace.request(target=target))


def test_relocation_refuses_foreign_content_in_the_target(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    workspace.target["materials"].mkdir(parents=True)
    (workspace.target["materials"] / "someone-else.mp4").write_bytes(b"foreign")
    with pytest.raises(RelocationError, match="拒绝混合两个工作区"):
        plan_relocation(workspace.request())


def test_relocation_refuses_a_symlink_in_the_source_tree(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    (workspace.source["materials"] / "link.mp4").symlink_to(workspace.media)
    with pytest.raises(RelocationError, match="符号链接"):
        plan_relocation(workspace.request())


def test_relocation_refuses_insufficient_target_space(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    with pytest.raises(RelocationError, match="剩余空间不足"):
        plan_relocation(workspace.request(free_bytes=lambda _path: 0))


def test_relocation_refuses_unfinished_execution_records(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    now = utc_now().isoformat()
    _execute(
        workspace.source["database"],
        "INSERT INTO job_runs(id,workspace_id,task_id,kind,idempotency_key,input_digest,"
        "status,progress,attempt,output_refs_json,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("job-198", WORKSPACE, "task-198", "render", "idem-198", "digest",
         "running", 10, 0, "[]", now, now),
    )
    with pytest.raises(RelocationError, match="未结束的执行记录"):
        plan_relocation(workspace.request())


def test_database_copy_rejects_a_target_with_different_rows(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    target = tmp_path / "copied.sqlite3"
    target.write_bytes(workspace.source["database"].read_bytes())
    _execute(target, "DELETE FROM material_segment_lineage")
    with pytest.raises(RelocationError, match="行数不一致"):
        _verify_database_copy(workspace.source["database"], target)


def test_resume_replaces_a_partial_target_file_after_verifying_source(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    run_relocation(workspace.request())
    # Simulate an interrupted/partial copy: the next run must not trust it.
    workspace.target_media.write_bytes(b"partial")
    receipt = run_relocation(workspace.request())
    assert receipt["status"] == "applied"
    assert workspace.target_media.read_bytes() == b"video-bytes-198"


def test_copy_file_refuses_to_overwrite_without_a_resume_receipt(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"real")
    target = tmp_path / "target.bin"
    target.write_bytes(b"other")
    with pytest.raises(RelocationError, match="拒绝覆盖"):
        _copy_file(source, target, resuming=False)
    assert target.read_bytes() == b"other"


def test_relocation_seals_identity_before_copy_and_resumes_same_backup(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)

    def interrupt(*args, **kwargs):
        persisted = json.loads(workspace.receipt_path.read_text())
        assert persisted["status"] == "backup"
        assert persisted["volume_identity"] == IDENTITY.as_record()
        assert not list(workspace.external.iterdir())
        raise OSError("interrupted before the first target write")

    with monkeypatch.context() as stop:
        stop.setattr(workspace_relocate, "_install_database", interrupt)
        with pytest.raises(OSError, match="interrupted"):
            run_relocation(workspace.request())
    checkpoint = json.loads(workspace.receipt_path.read_text())
    assert plan_relocation(workspace.request()).resuming

    receipt = run_relocation(workspace.request())

    assert receipt["status"] == "applied"
    assert receipt["operation_id"] == checkpoint["operation_id"]
    assert receipt["started_at"] == checkpoint["started_at"]
    assert receipt["backup"]["path"] == checkpoint["backup"]["path"]
    assert receipt["backup"]["sha256"] == checkpoint["backup"]["sha256"]
    assert receipt["backup"]["reused"] is True
    assert receipt["rebind"]["status"] == "applied"


@pytest.mark.parametrize("state", ["new", "partial", "legacy_applied"])
@pytest.mark.parametrize("failure", ["missing", "same_name"])
def test_relocation_rejects_wrong_target_before_receipt_or_target_writes(tmp_path, monkeypatch, state, failure):
    workspace = _build_workspace(tmp_path, monkeypatch)
    if state == "legacy_applied":
        receipt = run_relocation(workspace.request())
        del receipt["volume_identity"]
        workspace.receipt_path.write_text(json.dumps(receipt))
    elif state == "partial":
        plan = plan_relocation(workspace.request())
        workspace.receipt_path.write_text(json.dumps({
            "format": workspace_relocate.RECEIPT_FORMAT, "status": "backup",
            "operation_id": plan.operation_id, "target_volume": str(plan.target_volume),
            "source": {key: str(path) for key, path in plan.source.items()},
            "target": {key: str(path) for key, path in plan.target.items()},
            "volume_identity": IDENTITY.as_record(),
        }))
    else:
        volume_identity.save_volume_identity_record(workspace.record_path, [IDENTITY])
    request = workspace.request(
        is_external=lambda _path: failure != "missing",
        probe=lambda _path: replace(IDENTITY, uuid="different-volume"),
    )
    config_before = workspace.env_path.read_bytes()
    receipt_before = workspace.receipt_path.read_bytes() if workspace.receipt_path.exists() else None
    target_before = {p: p.read_bytes() for p in workspace.external.rglob("*") if p.is_file()}
    monkeypatch.setattr(workspace_relocate, "_acquire_execution_lock",
        lambda _path: pytest.fail("invalid volume reached the migration lock"))

    message = "不是已挂载的外接盘" if failure == "missing" else "身份不匹配"
    with pytest.raises(RelocationError, match=message):
        run_relocation(request)

    assert workspace.env_path.read_bytes() == config_before
    assert (workspace.receipt_path.read_bytes() if workspace.receipt_path.exists() else None) == receipt_before
    assert {p: p.read_bytes() for p in workspace.external.rglob("*") if p.is_file()} == target_before


def test_legacy_partial_receipt_does_not_infer_identity_from_mount_or_global_record(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    plan = plan_relocation(workspace.request())
    receipt = {
        "format": workspace_relocate.RECEIPT_FORMAT, "status": "copied",
        "operation_id": plan.operation_id, "target_volume": str(plan.target_volume),
        "source": {key: str(path) for key, path in plan.source.items()},
        "target": {key: str(path) for key, path in plan.target.items()},
    }
    original = json.dumps(receipt).encode()
    workspace.receipt_path.write_bytes(original)
    volume_identity.save_volume_identity_record(workspace.record_path, [IDENTITY])

    with pytest.raises(RelocationError, match="缺少可信目标卷身份"):
        run_relocation(workspace.request())

    assert workspace.receipt_path.read_bytes() == original
    assert not list(workspace.external.iterdir())
    assert not workspace.lock_path.exists()


def test_legacy_applied_receipt_reads_back_with_recorded_uuid_without_writes(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    receipt = run_relocation(workspace.request())
    del receipt["volume_identity"]
    original = json.dumps(receipt).encode()
    workspace.receipt_path.write_bytes(original)
    monkeypatch.setattr(workspace_relocate, "_write_receipt",
        lambda *_: pytest.fail("applied readback tried to rewrite the receipt"))

    assert run_relocation(workspace.request()) == {**receipt, "reapplied": True}
    assert workspace.receipt_path.read_bytes() == original

    workspace.target_media.write_bytes(b"incomplete target")
    with pytest.raises(RelocationError, match="旧迁移结果无法通过复核"):
        run_relocation(workspace.request())
    assert workspace.target_media.read_bytes() == b"incomplete target"
    assert workspace.receipt_path.read_bytes() == original


def test_relocation_compares_preflight_uuid_again_under_execution_lock(tmp_path, monkeypatch):
    workspace = _build_workspace(tmp_path, monkeypatch)
    observations = iter([IDENTITY, replace(IDENTITY, uuid="different-volume")])

    with pytest.raises(RelocationError, match="身份不匹配"):
        run_relocation(workspace.request(probe=lambda _volume: next(observations)))

    assert not workspace.receipt_path.exists()
    assert not list(workspace.external.iterdir())
    assert not (workspace.source["database"].parent / "backups").exists()


@pytest.mark.parametrize("field", ["receipt_path", "record_path"])
def test_relocation_keeps_identity_evidence_outside_target_volume(tmp_path, monkeypatch, field):
    workspace = _build_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(volume_identity, "volume_root", lambda _path: workspace.external)
    request = workspace.request(**{field: workspace.external / "control.json"})

    with pytest.raises(RelocationError, match="保存在目标卷之外"):
        run_relocation(request)

    assert not list(workspace.external.iterdir())
    assert not workspace.receipt_path.exists()
    assert not workspace.lock_path.exists()


@pytest.mark.parametrize("boundary", [
    "before_rebind", "before_commit_rebind", "before_config", "before_config_commit", "before_applied",
])
@pytest.mark.parametrize("failure", ["missing", "same_name"])
def test_relocation_stops_when_volume_changes_at_adoption_boundaries(tmp_path, monkeypatch, boundary, failure):
    workspace = _build_workspace(tmp_path, monkeypatch)
    changed = False
    config_before = workspace.env_path.read_bytes()
    port, name, status = {
        "before_rebind": (workspace_relocate, "_verify_database_copy", "copied"),
        "before_commit_rebind": (workspace_relocate.MaterialSourceFileAccess, "prepare_material_rebind", "copied"),
        "before_config": (workspace_relocate, "_rebind_material_library", "copied"),
        "before_config_commit": (workspace_relocate, "prepare_dotenv_values", "identity"),
        "before_applied": (workspace_relocate, "_update_dotenv", "identity"),
    }[boundary]
    original = getattr(port, name)

    def switch_after(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        changed = True
        return result

    monkeypatch.setattr(port, name, switch_after)
    message = "不是已挂载的外接盘" if failure == "missing" else "身份不匹配"
    with pytest.raises(RelocationError, match=message):
        run_relocation(workspace.request(
            is_external=lambda _volume: not changed or failure != "missing",
            probe=lambda _volume: replace(IDENTITY, uuid="different-volume") if changed else IDENTITY,
        ))

    receipt = json.loads(workspace.receipt_path.read_text())
    assert receipt["status"] == status
    assert receipt["volume_identity"] == IDENTITY.as_record()
    if boundary != "before_applied":
        assert workspace.env_path.read_bytes() == config_before
    if boundary in {"before_rebind", "before_commit_rebind", "before_config"}:
        assert not workspace.record_path.exists()
    if boundary == "before_config_commit":
        assert list(workspace.env_path.parent.iterdir()) == [workspace.env_path]
    if boundary in {"before_rebind", "before_commit_rebind"}:
        binding = _row(workspace.target["database"], "SELECT library_path FROM material_ingestion_binding")
        assert binding[0] == str(workspace.source["materials"].resolve())
    if boundary == "before_applied":
        assert str(workspace.target["database"]) in workspace.env_path.read_text()
        monkeypatch.setenv("AI_VIDEO_DIARY_DOTENV", str(workspace.env_path))
        monkeypatch.setattr(workspace_relocate, "_is_external_mount", lambda _path: True)
        monkeypatch.setattr(workspace_relocate, "_free_bytes", lambda _path: 10**12)
        monkeypatch.setattr(volume_identity, "probe_volume_identity", lambda _path: IDENTITY)
        monkeypatch.setattr(workspace_relocate, "_install_database", lambda *_args, **_kwargs:
            pytest.fail("config already switched; must not copy the database again"))
        request = _request_from_args(argparse.Namespace(
            target_root=str(workspace.external), database_target=None, materials_target=None,
            outputs_target=None, drafts_target=None, temporary_target=None, operation_id=None,
            receipt=str(workspace.receipt_path), volume_record=str(workspace.record_path),
        ))
        recovered = run_relocation(replace(request, lock_path=workspace.lock_path))
        assert recovered["status"] == "applied" and recovered["operation_id"] == receipt["operation_id"]
        assert recovered["volume_identity"] == IDENTITY.as_record()
