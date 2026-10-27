from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import os
import shutil
import sqlite3
import subprocess
import threading
import time

import pytest

from app.device_scheduler import DeviceScheduler, ProcessIdentity, SchedulePolicy, SubmissionIntent
from app.material_ingestion import (
    MaterialHashOwnerVerifier, MaterialHashServiceActor, MaterialHashUnitExecutor,
    MaterialIngestionService,
)
from app.production import CreationPlan, ProductionRepository
from app.material_hash import material_hash_candidate as _material_hash_candidate
from app.material_probe import MaterialProbeDependency, material_probe_unit_key
from app.creation.contracts import CreationTask, JobRun, TaskEvent
from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection, ScriptGenerationRun


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


def confirm_files(repo: ProductionRepository, library: Path) -> None:
    """Declare completion of the complete synthetic files used by these tests."""
    for path in library.glob("*.mp4"):
        if not path.is_symlink():
            repo.confirm_material_copy_complete(
                "one", relative_path=path.name,
                expected_revision=repo.material_copy_revision(path.stat()),
                confirmation_source="manual",
            )


class HashResources:
    def __init__(self, admit: bool | None = True):
        self.admit_value = admit

    def admit(self, request):
        from app.host_resource_probes import HostResourceDecision
        return HostResourceDecision(self.admit_value, dict(request.resources), {})

    def continue_unit(self, grant):
        return True

    def released(self, grant):
        return True


def test_copy_confirmation_persists_revision_before_waking() -> None:
    repository = Mock()
    service = MaterialIngestionService(repository)
    revision = {"size_bytes": "12", "mtime_ns": "100", "ctime_ns": "200", "inode": "3"}
    repository.confirm_material_copy_complete.side_effect = lambda *args, **kwargs: (
        pytest.fail("woke before persistence") if service._wake.is_set() else None
    )
    service.confirm_copy_complete(
        "one", relative_path="clip.mp4", expected_revision=revision,
        confirmation_source="manual",
    )
    repository.confirm_material_copy_complete.assert_called_once_with(
        "one", relative_path="clip.mp4", expected_revision=revision,
        confirmation_source="manual",
        verified_owner_user_id=None,
    )
    assert service._wake.is_set()


def test_copy_uploader_survives_restart_without_claiming_unknown_or_existing_assets(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    repo.bind_material_ingestion("one", expected_directory=str(library))
    repo.bind_material_ingestion("one", expected_directory=str(library), verified_owner_user_id="binder")
    assert repo.get_material_ingestion_owner() is None
    uploaded = library / "uploaded.mp4"
    uploaded.write_bytes(b"complete application copy")
    repo.confirm_material_copy_complete(
        "one", relative_path=uploaded.name,
        expected_revision=repo.material_copy_revision(uploaded.stat()),
        confirmation_source="app_import", verified_owner_user_id="uploader",
    )
    restarted = ProductionRepository(repo.database_path, library)
    asset = restarted.scan_library("one")[0]
    assert asset.owner_user_id == "uploader"
    restarted.confirm_material_copy_complete(
        "one", relative_path=uploaded.name,
        expected_revision=restarted.material_copy_revision(uploaded.stat()),
        confirmation_source="app_import", verified_owner_user_id="another-member",
    )
    assert restarted.scan_library("one")[0].owner_user_id == "uploader"

    external = library / "external.mp4"
    external.write_bytes(b"external file without uploader identity")
    restarted.confirm_material_copy_complete(
        "one", relative_path=external.name,
        expected_revision=restarted.material_copy_revision(external.stat()), confirmation_source="manual",
    )
    unknown = next(item for item in restarted.scan_library("one") if item.file_name == external.name)
    assert unknown.owner_user_id is None
    restarted.confirm_material_copy_complete(
        "one", relative_path=external.name,
        expected_revision=restarted.material_copy_revision(external.stat()),
        confirmation_source="app_import", verified_owner_user_id="another-member",
    )
    assert restarted.get_asset("one", unknown.id).owner_user_id is None
    assert next(item for item in restarted.scan_library("one") if item.id == unknown.id).owner_user_id is None


def test_copy_confirmation_failure_does_not_wake() -> None:
    repository = Mock()
    repository.confirm_material_copy_complete.side_effect = ValueError("文件修订已变化")
    service = MaterialIngestionService(repository)
    with pytest.raises(ValueError, match="修订已变化"):
        service.confirm_copy_complete(
            "one", relative_path="clip.mp4", expected_revision={},
            confirmation_source="app_import",
        )
    assert not service._wake.is_set()


class HashProcesses:
    def current(self):
        return ProcessIdentity(os.getpid(), f"test-{os.getpid()}", "test-boot")

    def children_released(self, children):
        return not children

    def recovery_state(self, holder, children):
        return "released" if not children else "unknown"


def hash_scheduler(repo: ProductionRepository, tmp_path: Path, *, resources=None) -> DeviceScheduler:
    return DeviceScheduler(
        repo.database_path, tmp_path / "device.lock", owners=MaterialHashOwnerVerifier(repo),
        resources=resources or HashResources(), processes=HashProcesses(),
    )


@pytest.fixture
def scheduled_hash_no_execution(monkeypatch):
    import _media_preview_test_fixtures as media_fixtures

    def forbidden(*_args, **_kwargs):
        raise AssertionError("hash fixture must not execute media, models or a bound driver")

    original_driver = MaterialHashUnitExecutor.execute

    def reject_bound_driver(executor, grant, scheduler):
        if executor._service is None:
            # Preserve the original unbound PermissionError gate, with no dispatch.
            return original_driver(executor, grant, scheduler)
        return forbidden()

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(media_fixtures, "write_synthetic_video", forbidden)
    monkeypatch.setattr(MaterialIngestionService, "start", forbidden)
    monkeypatch.setattr(MaterialIngestionService, "run_granted_probe", forbidden)
    monkeypatch.setattr(MaterialIngestionService, "run_granted_scene", forbidden)
    monkeypatch.setattr(MaterialHashUnitExecutor, "execute", reject_bound_driver)
    assert subprocess.Popen is forbidden
    assert media_fixtures.write_synthetic_video is forbidden
    assert MaterialIngestionService.run_granted_probe is forbidden


def test_scheduled_hash_requires_grant_and_persists_probe_dependency(tmp_path: Path, scheduled_hash_no_execution) -> None:
    duration = Mock(return_value=1.0)
    repo, library = make_repo(tmp_path, duration)
    source = library / "clip.mp4"
    source.write_bytes(b"bounded source")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    with pytest.raises(ValueError, match="必须显式注入"):
        MaterialIngestionService(repo, execution_mode="scheduled")
    scheduler = hash_scheduler(repo, tmp_path)
    service = MaterialIngestionService(
        repo, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0,
    )
    try:
        assert service.discover_and_submit_hashes() == []
        asset_id = repo.list_assets("one")[0].id
        premature = _material_hash_candidate(
            "one", str(library.resolve()), source.resolve(), source.stat(), asset_id,
        )
        with pytest.raises(PermissionError, match="不可提交"):
            scheduler.submit(
                SubmissionIntent("one", "material_asset", asset_id,
                                 premature.provisional_revision, premature.unit_key,
                                 SchedulePolicy()),
                MaterialHashServiceActor("one", str(library.resolve())),
            )
        request_ids = service.discover_and_submit_hashes()
        assert len(request_ids) == 1
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute("SELECT attempted_digest FROM material_ingestion_files").fetchone() == (None,)
        assert repo.get_asset("one", repo.list_assets("one")[0].id).analysis_status == "blocked"
        assert duration.call_count == 0

        grant = scheduler.pick_and_grant()
        assert grant is not None and grant.request_id == request_ids[0]
        executor = MaterialHashUnitExecutor()
        with pytest.raises(PermissionError, match="不属于"):
            executor.execute(grant, scheduler)
        executor.bind(service)
        assert service.run_granted_hash(grant) is not None
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute("SELECT attempted_digest, state FROM material_ingestion_files").fetchone() == (digest, "waiting")
            assert conn.execute("SELECT source_sha256, result_ref FROM material_analysis_checkpoints").fetchone() == (digest, f"sha256:{digest}")
            assert conn.execute(
                "SELECT input_digest, unit_key FROM device_work_requests "
                "WHERE owner_type='material_asset' AND unit_key LIKE 'probe-meta:v1:%'"
            ).fetchall() == [(digest, material_probe_unit_key(digest))]
        assert repo.get_asset("one", repo.list_assets("one")[0].id).analysis_status == "blocked"
        assert duration.call_count == 0  # Hash completion is not scene analysis or ready.
        assert service.discover_and_submit_hashes() == []
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM device_work_requests").fetchone() == (2,)
            assert conn.execute(
                "SELECT state FROM device_work_requests WHERE unit_key LIKE 'probe-meta:v1:%'"
            ).fetchone() == ("waiting",)
        with pytest.raises(PermissionError, match="不可提交"):
            scheduler.submit(
                SubmissionIntent("one", "material_asset", asset_id,
                                 premature.provisional_revision, premature.unit_key,
                                 SchedulePolicy()),
                MaterialHashServiceActor("one", str(library.resolve())),
            )
        restarted = ProductionRepository(repo.database_path, library, duration_reader=duration)
        assert restarted.probe_records.pending_material_probe_dependencies() == [
            MaterialProbeDependency("one", repo.list_assets("one")[0].id, digest,
                                    grant.input_digest),
        ]
    finally:
        scheduler.close()


def test_scheduled_hash_changed_source_defers_old_grant_and_keeps_workspace_owner(tmp_path: Path, scheduled_hash_no_execution) -> None:
    repo, library = make_repo(tmp_path)
    source = library / "clip.mp4"
    source.write_bytes(b"first")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    scheduler = hash_scheduler(repo, tmp_path)
    service = MaterialIngestionService(
        repo, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0,
    )
    try:
        service.discover_and_submit_hashes()
        service.discover_and_submit_hashes()
        grant = scheduler.pick_and_grant()
        assert grant is not None
        source.write_bytes(b"second version")
        assert service.run_granted_hash(grant) is None
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute("SELECT state, wait_reason FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone() == ("blocked", "input_stale")
            assert conn.execute("SELECT attempted_digest FROM material_ingestion_files").fetchone() == (None,)
            assert conn.execute("SELECT COUNT(*) FROM material_analysis_checkpoints").fetchone() == (0,)
        assert service.discover_and_submit_hashes() == []
        new_ids = service.discover_and_submit_hashes()
        assert len(new_ids) == 1 and new_ids[0] != grant.request_id
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute("SELECT DISTINCT workspace_id FROM device_work_requests").fetchall() == [("one",)]
    finally:
        scheduler.close()


def test_scheduled_hash_missing_library_defers_without_checkpoint(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    (library / "clip.mp4").write_bytes(b"source")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    scheduler = hash_scheduler(repo, tmp_path)
    service = MaterialIngestionService(
        repo, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0,
    )
    try:
        service.discover_and_submit_hashes()
        request_id = service.discover_and_submit_hashes()[0]
        grant = scheduler.pick_and_grant()
        assert grant is not None
        library.rename(tmp_path / "disconnected")
        assert service.run_granted_hash(grant) is None
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute(
                "SELECT state, wait_reason FROM device_work_requests WHERE id=?", (request_id,)
            ).fetchone() == ("blocked", "dependency_unavailable")
            assert conn.execute("SELECT COUNT(*) FROM material_analysis_checkpoints").fetchone() == (0,)
    finally:
        scheduler.close()


def test_scheduled_hash_scan_repairs_checkpoint_to_probe_submit_gap(tmp_path: Path, scheduled_hash_no_execution) -> None:
    repo, library = make_repo(tmp_path)
    (library / "clip.mp4").write_bytes(b"source")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    scheduler = hash_scheduler(repo, tmp_path)
    service = MaterialIngestionService(
        repo, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0,
    )
    try:
        service.discover_and_submit_hashes()
        service.discover_and_submit_hashes()
        grant = scheduler.pick_and_grant()
        assert grant is not None
        result = repo.hash_intake.hash_granted_material(grant, scheduler.assert_grant)
        scheduler.complete_unit(
            grant, checkpoint_database_path=repo.database_path,
            checkpoint_write=lambda conn, active: repo.hash_records.commit_material_hash(conn, active, result, source_verdict=repo.hash_intake.material_hash_verdict),
        )
        scheduler.close()
        restarted_repo = ProductionRepository(repo.database_path, library)
        scheduler = hash_scheduler(restarted_repo, tmp_path)
        service = MaterialIngestionService(
            restarted_repo, execution_mode="scheduled", scheduler=scheduler,
            stable_seconds=0,
        )
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM device_work_requests").fetchone() == (1,)
            conn.execute(
                "UPDATE material_analysis_checkpoints SET result_ref='sha256:invalid'"
            )
        assert service.discover_and_submit_hashes() == []
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM device_work_requests").fetchone() == (1,)
            conn.execute(
                "UPDATE material_analysis_checkpoints SET result_ref=?",
                (f"sha256:{result.source_sha256}",),
            )
        assert service.discover_and_submit_hashes() == []
        first_probe = service.submit_pending_probe_dependencies()
        assert len(first_probe) == 1
        assert service.discover_and_submit_hashes() == []
        assert service.submit_pending_probe_dependencies() == first_probe
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM device_work_requests").fetchone() == (2,)
            assert conn.execute(
                "SELECT input_digest FROM device_work_requests WHERE id=?", (first_probe[0],),
            ).fetchone() == (result.source_sha256,)
    finally:
        scheduler.close()


def test_scheduled_hash_unknown_resources_and_foreign_actor_fail_closed(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    (library / "clip.mp4").write_bytes(b"source")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    scheduler = hash_scheduler(repo, tmp_path, resources=HashResources(None))
    service = MaterialIngestionService(
        repo, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0,
    )
    try:
        service.discover_and_submit_hashes()
        request_id = service.discover_and_submit_hashes()[0]
        assert scheduler.pick_and_grant() is None
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute("SELECT state, wait_reason FROM device_work_requests WHERE id=?", (request_id,)).fetchone() == ("blocked", "resource_unknown")
            assert conn.execute("SELECT attempted_digest FROM material_ingestion_files").fetchone() == (None,)
            request = conn.execute("SELECT workspace_id, owner_type, owner_id, input_digest, unit_key FROM device_work_requests WHERE id=?", (request_id,)).fetchone()
        intent = SubmissionIntent(*request, SchedulePolicy())
        with pytest.raises(PermissionError, match="后台归属"):
            MaterialHashOwnerVerifier(repo).verify_submission(intent, MaterialHashServiceActor("other", str(library)))
    finally:
        scheduler.close()


def seed_creation_task(repo: ProductionRepository, workspace_id: str, task_id: str, *, script_stage: bool = False) -> None:
    plan = CreationPlan(
        id=f"plan-{task_id}", workspace_id=workspace_id, goal="并发制作", name="测试任务",
        target_count=1, language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
        knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="测试", model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint=f"fingerprint-{task_id}",
    )
    task = CreationTask(
        id=task_id, workspace_id=workspace_id, plan_id=plan.id, name=plan.name,
        scenario="knowledge_explanation", stage="script" if script_stage else "topic",
        selected_topic_id="topic-one" if script_stage else None,
    )
    repo.confirm_creation_plan(plan, task)


def test_unbound_copy_confirmation_restart_and_changed_content(tmp_path: Path) -> None:
    duration = Mock(return_value=1.0)
    repo, library = make_repo(tmp_path, duration)
    source = library / "clip.mp4"
    source.write_bytes(b"copying")
    service = MaterialIngestionService(repo, stable_seconds=0)
    assert service.scan_once() == []
    assert repo.list_assets("one") == []
    repo.bind_material_ingestion("one", expected_directory=str(library))
    assert service.scan_once() == []
    assert repo.list_assets("one") == []
    source.write_bytes(b"finished")
    assert service.scan_once() == []
    confirm_files(repo, library)
    analyzed = service.scan_once()
    assert len(analyzed) == 1 and analyzed[0].analysis_status == "ready"
    assert analyzed[0].review_status == "raw"
    repo.set_asset_visibility("one", analyzed[0].id, "shared")
    assert repo.search_material_segments("one")
    assert duration.call_count == 1
    assert service.scan_once() == []

    restarted = ProductionRepository(tmp_path / "production.sqlite", library, duration_reader=duration, scene_reader=lambda path, seconds: [])
    assert MaterialIngestionService(restarted, stable_seconds=0).scan_once() == []
    assert duration.call_count == 1
    prior_stat = source.stat()
    time.sleep(0.01)
    source.write_bytes(b"changed!")  # Same size/name/mtime, but ctime exposes the rewrite.
    os.utime(source, ns=(prior_stat.st_atime_ns, prior_stat.st_mtime_ns))
    assert source.stat().st_ctime_ns != prior_stat.st_ctime_ns
    changed_service = MaterialIngestionService(restarted, stable_seconds=0)
    assert changed_service.scan_once() == []
    assert changed_service.scan_once() == []
    confirm_files(restarted, library)
    assert len(changed_service.scan_once()) == 1
    assert duration.call_count == 2
    assert restarted.list_assets("other") == []
    with pytest.raises(ValueError, match="已绑定另一工作区"):
        restarted.bind_material_ingestion("other", expected_directory=str(library))


def test_failure_is_isolated_and_retry_is_explicit(tmp_path: Path) -> None:
    def duration(path: Path) -> float:
        if path.name == "bad.mp4":
            raise ValueError("broken")
        return 1.0

    repo, library = make_repo(tmp_path, duration)
    (library / "good.mp4").write_bytes(b"good")
    (library / "bad.mp4").write_bytes(b"bad")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    confirm_files(repo, library)
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    service.scan_once()
    assets = {asset.file_name: asset for asset in repo.list_assets("one")}
    assert assets["good.mp4"].analysis_status == "ready"
    assert assets["bad.mp4"].analysis_status == "failed"
    assert service.scan_once() == []
    repo.retry_material_ingestion("one", assets["bad.mp4"].id)
    assert service.scan_once()[0].analysis_status == "failed"


def test_unchanged_ready_and_failed_files_do_not_rehash_each_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def duration(path: Path) -> float:
        if path.name == "failed.mp4":
            raise ValueError("broken")
        return 1.0

    repo, library = make_repo(tmp_path, duration)
    (library / "ready.mp4").write_bytes(b"ready")
    (library / "failed.mp4").write_bytes(b"failed")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    confirm_files(repo, library)
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    service.scan_once()
    original_digest = hashlib.file_digest
    digests = Mock(wraps=original_digest)
    monkeypatch.setattr("app.production.hashlib.file_digest", digests)
    for _ in range(4):
        assert service.scan_once() == []
    assert digests.call_count == 0


def test_existing_ingestion_observations_gain_lightweight_stat_columns(tmp_path: Path) -> None:
    library = tmp_path / "曜核AI IP一体机"
    library.mkdir()
    database = tmp_path / "production.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE material_ingestion_files (workspace_id TEXT NOT NULL, file_path TEXT NOT NULL, "
            "size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, observed_at REAL NOT NULL, "
            "completed_digest TEXT, attempted_digest TEXT, state TEXT NOT NULL, "
            "PRIMARY KEY (workspace_id, file_path))"
        )
        conn.execute(
            "INSERT INTO material_ingestion_files (workspace_id, file_path, size_bytes, mtime_ns, observed_at, state) VALUES ('one', 'clip.mp4', 5, 10, 20, 'ready')"
        )
    ProductionRepository(database, library)
    with sqlite3.connect(database) as conn:
        row = conn.execute("SELECT size_bytes, ctime_ns, inode FROM material_ingestion_files").fetchone()
    assert row == (5, 0, 0)


def test_copy_waits_for_confirmation_instead_of_stable_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    duration = Mock(return_value=1.0)
    repo, library = make_repo(tmp_path, duration)
    source = library / "copy.mp4"
    source.write_bytes(b"part")
    repo.bind_material_ingestion("one", expected_directory=str(library))
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
    assert service.scan_once() == []
    assert duration.call_count == 0
    confirm_files(repo, library)
    assert service.scan_once()[0].analysis_status == "ready"


def test_binding_wakes_background_scan_without_waiting_for_interval(tmp_path: Path) -> None:
    analyzed = threading.Event()

    def duration(path: Path) -> float:
        analyzed.set()
        return 1.0

    repo, library = make_repo(tmp_path, duration)
    (library / "new.mp4").write_bytes(b"video")
    service = MaterialIngestionService(repo, scan_interval_seconds=3600, stable_seconds=0)
    service.start()
    try:
        repo.bind_material_ingestion("one", expected_directory=str(library))
        service.wake()
        deadline = time.monotonic() + 2
        while not repo.pending_material_copies("one") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert repo.pending_material_copies("one")
        assert not analyzed.is_set()
        service.confirm_copy_complete(
            "one", relative_path="new.mp4",
            expected_revision=repo.material_copy_revision((library / "new.mp4").stat()),
            confirmation_source="manual",
        )
        assert analyzed.wait(2)
    finally:
        service.stop()
    assert repo.list_assets("one")[0].analysis_status == "ready"


def test_missing_disk_and_symlink_escape(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    (library / "clip.mp4").write_bytes(b"video")
    outside = tmp_path / "private.mp4"
    outside.write_bytes(b"outside")
    (library / "escape.mp4").symlink_to(outside)
    repo.bind_material_ingestion("one", expected_directory=str(library))
    confirm_files(repo, library)
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


def test_ingestion_state_counts_are_workspace_scoped_and_path_free(tmp_path: Path) -> None:
    def duration(path: Path) -> float:
        if path.name == "failed.mp4":
            raise ValueError("broken")
        return 1.0

    repo, library = make_repo(tmp_path, duration)
    (library / "ready.mp4").write_bytes(b"ready")
    (library / "failed.mp4").write_bytes(b"failed")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    confirm_files(repo, library)
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    service.scan_once()
    (library / "copying.mp4").write_bytes(b"partial")
    service.scan_once()
    assert repo.material_ingestion_state_counts("one") == {
        "observing": 1, "running": 0, "ready": 1, "failed": 1, "blocked_disk": 0,
    }
    assert all(value == 0 for value in repo.material_ingestion_state_counts("other").values())
    library.rename(tmp_path / "disconnected")
    service.scan_once()
    assert repo.material_ingestion_state_counts("one") == {
        "observing": 1, "running": 0, "ready": 0, "failed": 1, "blocked_disk": 1,
    }


def test_directory_state_and_confirmation_reject_stale_or_foreign_binding(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    assert repo.material_ingestion_directory_state("one") == {
        "directory": str(library), "status": "ready", "bound_to_current_workspace": False,
        "bound_to_another_workspace": False, "configured_path_changed": False,
    }
    with pytest.raises(ValueError, match="配置已变化"):
        repo.bind_material_ingestion("one", expected_directory=str(tmp_path / "stale"))
    repo.bind_material_ingestion("one", expected_directory=str(library))
    repo.bind_material_ingestion("one", expected_directory=str(library))
    assert repo.material_ingestion_directory_state("one")["bound_to_current_workspace"] is True
    foreign = repo.material_ingestion_directory_state("other")
    assert foreign["directory"] is None
    assert foreign["bound_to_another_workspace"] is True
    assert str(library) not in repr(foreign)
    with pytest.raises(ValueError, match="已绑定另一工作区"):
        repo.bind_material_ingestion("other", expected_directory=str(library))

    alternate = tmp_path / "another"
    alternate.mkdir()
    reconfigured = ProductionRepository(tmp_path / "production.sqlite", alternate)
    changed = reconfigured.material_ingestion_directory_state("one")
    assert changed["configured_path_changed"] is True
    assert changed["bound_to_current_workspace"] is False
    with pytest.raises(ValueError, match="配置已变化"):
        reconfigured.bind_material_ingestion("one", expected_directory=str(library))
    with pytest.raises(ValueError, match="已绑定另一工作区或目录"):
        reconfigured.bind_material_ingestion("one", expected_directory=str(alternate))

    missing = ProductionRepository(tmp_path / "missing.sqlite", tmp_path / "absent")
    assert missing.material_ingestion_directory_state("one")["status"] == "missing"
    invalid_file = tmp_path / "plain-file"
    invalid_file.write_text("not a directory")
    invalid = ProductionRepository(tmp_path / "invalid.sqlite", invalid_file)
    assert invalid.material_ingestion_directory_state("one")["status"] == "invalid"
    unmounted = ProductionRepository(tmp_path / "unmounted.sqlite", "/Volumes/codex-mat01-unmounted/inbox")
    assert unmounted.material_ingestion_directory_state("one")["status"] == "unmounted"

    (library / "video.mp4").write_bytes(b"fixture")
    confirm_files(repo, library)
    service = MaterialIngestionService(repo, stable_seconds=0)
    assert service.scan_once()[0].analysis_status == "ready"


def test_material_failure_messages_do_not_expose_exception_paths(tmp_path: Path) -> None:
    secret_path = "/Users/someone/private/token-and-video.mp4"

    def bad_media(path: Path) -> float:
        raise ValueError(f"ffprobe failed on {secret_path}")

    repo, library = make_repo(tmp_path, bad_media)
    (library / "bad.mp4").write_bytes(b"broken")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    confirm_files(repo, library)
    service = MaterialIngestionService(repo, stable_seconds=0)
    asset = service.scan_once()[0]
    assert asset.analysis_status == "failed"
    assert "格式或内容无效" in asset.analysis_message
    assert secret_path not in asset.analysis_message

    repo.fail_material_ingestion("one", asset.id, "digest", RuntimeError(f"worker failed at {secret_path}"))
    persisted = repo.get_asset("one", asset.id)
    assert "本机日志" in persisted.analysis_message
    assert secret_path not in persisted.analysis_message


def test_reanalysis_preserves_unmatched_manual_correction(tmp_path: Path) -> None:
    boundaries = [2.0]
    repo, library = make_repo(tmp_path, duration_reader=lambda path: 4.0, scene_reader=lambda path, duration: boundaries)
    source = library / "clip.mp4"
    source.write_bytes(b"video")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    confirm_files(repo, library)
    service = MaterialIngestionService(repo, stable_seconds=0)
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
    repo.bind_material_ingestion("one", expected_directory=str(library))
    confirm_files(repo, library)
    service = MaterialIngestionService(repo, stable_seconds=0)
    service.scan_once()
    service.scan_once()
    ids = [asset.id for asset in repo.list_assets("one")]
    for asset_id in ids:
        repo.set_asset_visibility("one", asset_id, "shared")
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


def test_segment_usage_change_updates_persisted_tags_and_search(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    (library / "one.mp4").write_bytes(b"one")
    (library / "two.mp4").write_bytes(b"two")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    confirm_files(repo, library)
    assets = repo.scan_library("one")
    for asset in assets:
        repo.analyze_asset("one", asset.id)
        repo.update_asset_review("one", asset.id, review_status="approved", usage_role="b_roll")
        segment = repo.get_asset("one", asset.id).segments[0]
        repo.update_segment(
            "one", asset.id, segment.id,
            description="人工描述", transcript="人工转录", review_status="approved",
            usage_role="b_roll", user_tags=["手工标签"],
        )
    for asset in assets:
        repo.set_asset_visibility("one", asset.id, "shared")
    first = repo.get_asset("one", assets[0].id)
    other = repo.get_asset("one", assets[1].id)
    original = first.segments[0]
    assert "用途:b_roll" in original.system_tags

    repo.update_segment(
        "one", first.id, original.id,
        description=original.description, transcript=original.transcript,
        review_status=original.review_status, usage_role="a_roll",
    )
    saved = repo.get_asset("one", first.id).segments[0]
    assert saved.usage_role == "a_roll"
    assert "用途:a_roll" in saved.system_tags
    assert "用途:b_roll" not in saved.system_tags
    assert (saved.description, saved.transcript, saved.user_tags) == (
        original.description, original.transcript, original.user_tags,
    )
    assert repo.get_asset("one", other.id) == other
    assert [match.segment_ref for match in repo.search_material_segments(
        "one", tags=["用途:a_roll"],
    )] == [f"segment:{original.id}"]
    assert [match.segment_ref for match in repo.search_material_segments(
        "one", tags=["用途:b_roll"],
    )] == [f"segment:{other.segments[0].id}"]


def test_manual_edit_during_analysis_survives_merge(tmp_path: Path) -> None:
    repo, library = make_repo(tmp_path)
    (library / "clip.mp4").write_bytes(b"video")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    confirm_files(repo, library)
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
    real_repo.bind_material_ingestion("one", expected_directory=str(library))
    confirm_files(real_repo, library)
    service = MaterialIngestionService(real_repo, stable_seconds=0)
    analyzed = service.scan_once()
    assert len(analyzed) == 1
    assert analyzed[0].analysis_status == "ready"
    assert analyzed[0].duration_seconds == pytest.approx(1.2, abs=0.2)
    assert analyzed[0].segments


def test_concurrent_job_creation_reuses_same_persisted_idempotency_key(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    seed_creation_task(repo, "one", "task-one")
    gate = threading.Barrier(12)

    def create(index: int) -> JobRun:
        gate.wait()
        return repo.create_job_run(JobRun(
            id=f"job-{index}", workspace_id="one", task_id="task-one",
            kind="creation_task_execution", idempotency_key="same-request", input_digest="same-input",
        ))

    with ThreadPoolExecutor(max_workers=12) as pool:
        created = list(pool.map(create, range(12)))
    assert len({job.id for job in created}) == 1
    assert len(repo.list_job_runs("one", "task-one")) == 1
    with pytest.raises(ValueError, match="不同的任务输入"):
        repo.create_job_run(JobRun(
            id="different-input", workspace_id="one", task_id="task-one",
            kind="creation_task_execution", idempotency_key="same-request", input_digest="changed",
        ))
    with pytest.raises(ValueError, match="ID 已存在"):
        repo.create_job_run(JobRun(
            id=created[0].id, workspace_id="one", task_id="task-one",
            kind="creation_task_execution", idempotency_key="different-request", input_digest="different",
        ))
    seed_creation_task(repo, "two", "task-two")
    other = repo.create_job_run(JobRun(
        id="other-workspace-job", workspace_id="two", task_id="task-two",
        kind="creation_task_execution", idempotency_key="same-request", input_digest="same-input",
    ))
    assert other.id != created[0].id


def test_concurrent_identical_task_event_returns_one_record(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    seed_creation_task(repo, "one", "task-one")
    job = repo.create_job_run(JobRun(
        id="job-one", workspace_id="one", task_id="task-one", kind="creation_task_execution",
        idempotency_key="request-one", input_digest="input-one",
    ))
    event = TaskEvent(
        id="start-one", workspace_id="one", task_id="task-one", job_id=job.id,
        event_type="creation_started", payload={"job_id": job.id}, created_at=job.created_at,
    )
    gate = threading.Barrier(12)

    def append(_: int) -> TaskEvent:
        gate.wait()
        return repo.append_task_event(event)

    with ThreadPoolExecutor(max_workers=12) as pool:
        events = list(pool.map(append, range(12)))
    assert all(item == event for item in events)
    assert repo.list_task_events("one", "task-one") == [event]
    with pytest.raises(ValueError, match="领域字段不一致"):
        repo.append_task_event(replace(event, payload={"job_id": "different"}))
    seed_creation_task(repo, "two", "task-two")
    with pytest.raises(ValueError, match="领域字段不一致"):
        repo.append_task_event(replace(event, workspace_id="two", task_id="task-two", job_id=None))


def test_assistant_draft_update_uses_current_run_and_keeps_confirmation_boundary(tmp_path: Path) -> None:
    repo, _ = make_repo(tmp_path)
    seed_creation_task(repo, "one", "task-one", script_stage=True)
    run = ScriptGenerationRun(
        id="run-one", workspace_id="one", task_id="task-one", plan_id="plan-task-one",
        topic_id="topic-one", status="running", route_snapshot={"source": "pi"},
        prompt_schema_version="assistant-v1", input_references=[{"reference_id": "ref-one"}],
    )
    repo.start_script_generation(run)
    initial = CreationTaskScript(
        id="draft-one", workspace_id="one", task_id="task-one", run_id=run.id,
        topic_id="topic-one", title="初稿", language="zh-CN",
        sections=[CreationTaskScriptSection("section-one", "开头", "初稿内容", "真实画面", ["ref-one"])],
    )
    repo.complete_script_generation(run.id, initial)
    revised_sections = [CreationTaskScriptSection("section-one", "开头", "用户改过的内容", "产品画面", ["ref-one"])]
    revised = repo.update_assistant_script_draft(
        "one", "task-one", initial.id, title="新版草稿", sections=revised_sections,
        expected_updated_at=initial.updated_at,
    )
    assert (revised.id, revised.run_id, revised.topic_id, revised.selection_status) == (initial.id, run.id, "topic-one", "draft")
    assert revised.sections == revised_sections
    assert repo.get_creation_task("one", "task-one").confirmed_script_id is None
    assert repo.get_creation_task("one", "task-one").stage_status == "waiting_for_user"
    restarted = ProductionRepository(tmp_path / "production.sqlite", repo.video_library_path)
    assert restarted.get_creation_task_script("one", initial.id).title == "新版草稿"
    with pytest.raises(KeyError):
        restarted.update_assistant_script_draft("other", "task-one", initial.id, title="越界", sections=revised_sections)
    with pytest.raises(ValueError, match="模型输入之外"):
        restarted.update_assistant_script_draft(
            "one", "task-one", initial.id, title="伪来源",
            sections=[replace(revised_sections[0], evidence_reference_ids=["unknown"])],
            expected_updated_at=revised.updated_at,
        )
    restarted.confirm_creation_task_script(
        "one", "task-one", initial.id, title="用户确认", sections=revised_sections,
        expected_updated_at=revised.updated_at,
    )
    confirmed = restarted.get_creation_task_script("one", initial.id)
    with pytest.raises(ValueError, match="不允许修改"):
        restarted.update_assistant_script_draft(
            "one", "task-one", initial.id, title="越过确认", sections=revised_sections,
            expected_updated_at=confirmed.updated_at,
        )


def test_scheduled_hash_checkpoint_sql_refusal_rolls_back_observation_and_permit(tmp_path, scheduled_hash_no_execution):
    from app.material_hash import material_hash_candidate
    from app.materials.hash_intake import MaterialHashIntake
    from app.materials.hash_records import MaterialHashRecordStore

    repo, library = make_repo(tmp_path)
    assert isinstance(repo.hash_intake, MaterialHashIntake)
    assert isinstance(repo.hash_records, MaterialHashRecordStore)
    assert repo.probe_records._hash_candidate_from_stat is material_hash_candidate
    (library / "clip.mp4").write_bytes(b"isolated permit hash checkpoint")
    repo.bind_material_ingestion("one", expected_directory=str(library))
    scheduler = hash_scheduler(repo, tmp_path)
    service = MaterialIngestionService(repo, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0)
    try:
        service.discover_and_submit_hashes()
        request_ids = service.discover_and_submit_hashes()
        assert len(request_ids) == 1
        grant = scheduler.pick_and_grant()
        assert grant is not None and grant.request_id == request_ids[0]
        result = repo.hash_intake.hash_granted_material(grant, scheduler.assert_grant)
        with sqlite3.connect(repo.database_path) as conn:
            original_observation = conn.execute("SELECT * FROM material_ingestion_files").fetchone()
            original_request = conn.execute("SELECT * FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone()
            original_permit = conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone()
            conn.execute("""CREATE TRIGGER refuse_hash_checkpoint BEFORE INSERT ON material_analysis_checkpoints
                WHEN NEW.unit_key LIKE 'hash:v1:%' AND EXISTS(
                    SELECT 1 FROM material_ingestion_files WHERE workspace_id=NEW.workspace_id
                    AND attempted_digest=NEW.source_sha256 AND state='waiting')
                BEGIN SELECT RAISE(ABORT, 'isolated hash checkpoint refusal'); END""")
        checked = []
        original_verdict = repo.hash_intake.material_hash_verdict

        def verify_original_source(workspace_id, asset_id, input_digest, unit_key, connection):
            assert connection.in_transaction
            checked.append(connection)
            return original_verdict(workspace_id, asset_id, input_digest, unit_key, connection)

        with pytest.raises(sqlite3.IntegrityError, match="isolated hash checkpoint refusal"):
            scheduler.complete_unit(grant, checkpoint_database_path=repo.database_path,
                checkpoint_write=lambda conn, active: repo.hash_records.commit_material_hash(
                    conn, active, result, source_verdict=verify_original_source))
        assert checked
        with sqlite3.connect(repo.database_path) as conn:
            assert conn.execute("SELECT * FROM material_ingestion_files").fetchone() == original_observation
            assert conn.execute("SELECT * FROM device_work_requests WHERE id=?", (grant.request_id,)).fetchone() == original_request
            assert conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone() == original_permit
            assert conn.execute("SELECT COUNT(*) FROM material_analysis_checkpoints").fetchone() == (0,)
            assert conn.execute("SELECT COUNT(*) FROM device_work_requests").fetchone() == (1,)
    finally:
        scheduler.close()
