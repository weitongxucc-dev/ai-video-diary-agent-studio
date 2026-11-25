"""Opt-in replay of a retained isolated preparation, without recalibrating it."""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import psutil
import pytest

from app import main
from app.artifact_store import ArtifactStore
from app.creation.contracts import TimelineVersion
from app.creation.task_authorization import TaskAuthorizationUseCase
from app.creation_execution import CreationExecutionActor
from app.device_resource_configuration import current_device_identity
from app.device_resource_preparation import DeviceResourcePreparation
from app.device_resource_profile import host_budget_from_payload
from app.host_resource_probes import HostBudget, HostProcessProbe, HostResourceProbe
from app.local_members import LocalMemberService
from app.material_ingestion import MaterialIngestionService
from app.media_execution import TaskMediaExecutionDelegate, build_media_execution_entrypoint
from app.media_execution.bound_cut import BOUND_CUT_FACT, BOUND_CUT_KIND, BoundMp4CutUseCase
from app.media_execution.cut_disk import CutDiskProgress
from app.media_execution.cut_measurement import copy_verified_cut_output
from app.production import MaterialSegment
from app.quality_worker_runtime import budget_identity
from test_host_resource_probes import _render_budget
from test_material_ingestion import hash_scheduler
from test_task_media_grants import case


def test_cut_disk_progress_rejects_a_replacement_at_its_owned_path(tmp_path):
    progress = CutDiskProgress(tmp_path, source_bytes=8192, output_bytes=16384)
    snapshot = tmp_path / "source.mp4"
    with snapshot.open("xb") as target:
        progress.bind("source", snapshot, target)
        target.write(b"x" * 4096)
    allocated = min(8192, snapshot.stat().st_blocks * 512)
    assert allocated > 0 and progress.remaining_bytes() == 40960 - allocated

    snapshot.rename(tmp_path / "original.mp4")
    snapshot.write_bytes(b"y" * 4096)
    with pytest.raises(ValueError, match="文件身份"):
        progress.remaining_bytes()


def test_cut_admission_cannot_use_unselected_or_changed_preparation(tmp_path, monkeypatch):
    monkeypatch.setattr("app.host_resource_probes.sys.platform", "darwin")
    render = _render_budget()
    roles = {role: tmp_path / role for role in ("database", "output", "temporary")}
    host = HostResourceProbe(tmp_path / "isolated.sqlite", disk_paths=roles,
        budget=HostBudget(100, 0, None, 0, None, 4096, cpu_render=render))
    request = SimpleNamespace(id="cut", owner_type="creation_job", owner_id="job_media_cut",
        unit_key="task_media:digest", input_digest="digest", resources={
            "kind": BOUND_CUT_KIND, "execution_mode": "cpu_ffmpeg", "threads": 1,
            "budget_digest": budget_identity(render), "cpu_policy": asdict(render.running_cpu_policy),
            "source_seconds": 1., "duration_seconds": .5, "timeout_seconds": 5.,
            "width_px": 320, "height_px": 240, "input_bytes": 2000, "work_bytes": 6000,
            "max_output_bytes": 4000, "output_root": str(roles["output"]),
        })
    assert host.admit(request).reason == "cut_resource_fields_invalid"
    request.resources["preparation_checkpoint"] = "device-resource-preparation:" + "a" * 64
    assert host.admit(request).reason == "declared_resources_exceed_budget"
    request.resources["budget_digest"] = "other-budget"
    assert host.admit(request).reason == "cut_cpu_budget_changed"
    request.owner_type = "material_asset"
    assert host.admit(request).reason == "cut_owner_invalid"


@pytest.fixture
def retained_preparation():
    keys = ("YAOHE_CUT_REPLAY_ROOT", "YAOHE_CUT_REPLAY_REQUEST", "YAOHE_CUT_REPLAY_SOURCE")
    if not all(os.getenv(key) for key in keys):
        pytest.skip("requires retained isolated preparation and authorized synthetic source")
    root, request_id, source = (os.environ[key] for key in keys)
    return Path(root), request_id, Path(source)


def _copy_completed_preparation(repo, root, request_id, profile):
    original = (root / "db" / "production.sqlite").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(original, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM device_work_requests WHERE id=? AND state='done'", (request_id,)).fetchone())
    resources = "resource-preparations"
    for kind, name in (("plans", f'{row["owner_id"]}.json'),
                       ("receipts", f'{request_id}-{row["last_grant_generation"]}.json')):
        target = profile.parent / resources / kind / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "profile" / resources / kind / name, target)
    with repo.connection_factory() as conn:
        # Both schemas come from this checkout's original device scheduler.
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO device_work_requests ({columns}) VALUES ({placeholders})", tuple(row.values()))
    return row


def _ingest(repo, actor, path, tmp_path):
    repo.material_source_files.bind_material_ingestion("local", expected_directory=str(repo.video_library_path),
        verified_owner_user_id=actor.member_id)
    repo.material_source_files.confirm_material_copy_complete("local", relative_path=path.name,
        expected_revision=repo.material_source_files.material_copy_revision(path.stat()),
        verified_owner_user_id=actor.member_id, confirmation_source="app_import")
    scheduler = hash_scheduler(repo, tmp_path)
    scheduler.processes = HostProcessProbe()
    intake = MaterialIngestionService(repo, execution_mode="scheduled", scheduler=scheduler, stable_seconds=0)
    try:
        intake.discover_and_submit_hashes()
        waiting, = intake.discover_and_submit_hashes()
        grant = scheduler.pick_and_grant()
        assert grant.request_id == waiting
        digest = intake.run_granted_hash(grant)
        grant = scheduler.pick_and_grant()
        assert grant.unit_key.startswith("probe-meta:")
        assert intake.run_granted_probe(grant)
        assert scheduler.shutdown_released()
    finally:
        scheduler.close()
    asset = repo.get_asset("local", grant.owner_id)
    # Fixture editorial readiness, after the genuine scheduled hash and probe.
    asset = repo._save_asset(replace(asset, analysis_status="ready", usage_role="b_roll",
        segments=[MaterialSegment("qa-cut-segment", asset.id, 0, 2, usage_role="b_roll")]))
    return asset, digest


@pytest.mark.parametrize("outcome", [
    "published", "missing_receipt", "changed_tools", "changed_source", "no_access",
    "no_selection", "recorded_budget", "low_disk", "changed_output", "boundary_disk", "external_disk",
])
def test_cut_replays_an_existing_completed_preparation(tmp_path, monkeypatch, retained_preparation, outcome):
    preparation_root, prep_request, existing_source = retained_preparation
    profile_payload = json.loads((preparation_root / "profile" / "resource.json").read_text())
    recorded_budget = host_budget_from_payload(profile_payload["budget"])
    render = recorded_budget.cpu_render
    host_budget = recorded_budget
    if outcome != "recorded_budget":
        # Exercise the real host checks with explicit fixture capacity. The
        # measured sample itself never supplies a maximum or raises a threshold.
        render = replace(render, max_pixel_seconds=1280 * 720 * 6, max_output_bytes=4 * 1024**2,
                         additional_disk_bytes=8 * 1024**2)
        host_budget = replace(recorded_budget, sample_seconds=.02, cpu_render=render)
    write_paths = {"database": tmp_path, "output": tmp_path / "artifacts", "temporary": tmp_path / "scratch"}
    host = HostResourceProbe(tmp_path / "media.sqlite", disk_paths=write_paths, budget=host_budget)
    repo, actor, _, _, build, media = case(tmp_path, resources=host)
    members = LocalMemberService(repo.database_path)
    _, token = members.whoami(None)
    profile = tmp_path / "prep" / "resource.json"
    row = _copy_completed_preparation(repo, preparation_root, prep_request, profile)
    assert row["member_id"] == actor.member_id
    source = repo.video_library_path / "qa-source.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(existing_source, source)
    asset, digest = _ingest(repo, actor, source, tmp_path)
    reference = repo.workspace_media_reference_for_asset("local", asset.id)
    with repo.connection_factory() as conn:
        repo._claim_workspace_media_reference(conn, workspace_id="local", reference=reference)
    base = repo.timeline_records.create_timeline_version(TimelineVersion("qa-base", "local", "task", None, 1,
        {"montage": {"clips": [{"asset_id": asset.id, "source_path": reference}]}}, None))
    auth = TaskAuthorizationUseCase(repo, lambda identity: media.verify_identity(
        CreationExecutionActor.from_event("local", identity)))
    media.task_authorizations = auth
    auth.ensure(repo.task_records.get_creation_task("local", "task"), actor, interactive=True)
    preparation = DeviceResourcePreparation(profile_path=profile,
        device=current_device_identity({"DEVICE_ID": "isolated-cut-preparation"}), settings=main.settings,
        write_paths=write_paths,
        verify_identity=media.verify_identity)
    witness = preparation.read_mp4_cut_measurement(database_path=repo.database_path, request_id=prep_request, actor=actor)
    assert witness["measurement"]["source"]["sha256"] == digest and witness["checkpoint"] == row["checkpoint_ref"]
    store = ArtifactStore(tmp_path / "artifacts")
    entrypoint = build_media_execution_entrypoint(repository=repo, output_root=tmp_path / "artifacts",
        connection_factory=repo.connection_factory, artifact_store=store)
    media.media_execution_delegate = TaskMediaExecutionDelegate(entrypoint,
        bounded_cut=BoundMp4CutUseCase(repo, lambda: preparation, auth))
    monkeypatch.setattr(main, "production_repo", repo)
    monkeypatch.setattr(main, "artifact_store", store)
    monkeypatch.setattr(main, "local_members_service", members)
    monkeypatch.setattr(main, "settings", replace(main.settings, database_path=str(repo.database_path),
        video_output_path=str(tmp_path / "artifacts"), video_inbox_path=str(repo.video_library_path),
        temporary_root_path=str(tmp_path / "scratch")))
    monkeypatch.setattr(main, "_device_operation_budget", lambda key: render if key == "cpu_render" else None)
    monkeypatch.setattr(main, "_device_preparation_service", lambda: preparation)
    monkeypatch.setattr(main, "_task_authorizations_for_goal", lambda: auth)
    media.facts = main._task_media_facts
    runtime = build(media)
    runtime.scheduler.processes = HostProcessProbe()
    registered = []
    register_child = runtime.scheduler.register_child

    def record(grant, child):
        register_child(grant, child)
        registered.append((grant, child))

    monkeypatch.setattr(runtime.scheduler, "register_child", record)
    monkeypatch.setattr(main, "_creation_runtime", lambda: runtime)
    monkeypatch.setattr(main, "_media_result_read_components", lambda: (media, runtime.scheduler))
    client = TestClient(main.app, headers={"X-Local-Member-Token": token})
    request = {"request_id": str(uuid4()), "base_version_id": base.id, "asset_id": asset.id,
               "start_seconds": .25, "end_seconds": 1.25, "preparation_request_id": prep_request}
    url = "/api/creation-tasks/task/media-jobs"
    disk_samples = []
    try:
        if outcome == "missing_receipt":
            (preparation.root / "receipts" / f"{prep_request}-{witness['generation']}.json").unlink()
        elif outcome == "changed_tools":
            monkeypatch.setattr("app.device_resource_preparation._hash_file", lambda _: "0" * 64)
        elif outcome == "changed_source":
            with source.open("r+b") as handle:
                handle.seek(4)
                handle.write(b"xxxx")
        elif outcome == "no_access":
            repo._save_asset(replace(asset, visibility="personal", owner_user_id="other-member"))
        elif outcome == "no_selection":
            request.pop("preparation_request_id")
        elif outcome == "low_disk":
            # Fit staging alone but fail its publication-copy reservation.
            free = host_budget.min_free_disk_bytes + asset.size_bytes + render.max_output_bytes
            monkeypatch.setattr(psutil, "disk_usage", lambda _: SimpleNamespace(free=free))
        elif outcome in {"boundary_disk", "external_disk"}:
            initial = host_budget.min_free_disk_bytes + asset.size_bytes + 2 * render.max_output_bytes
            external = store.storage_root / "other-job.mp4"

            def bounded_disk_usage(_):
                live = list(store.storage_root.glob("media_*/mp4-cut-*/*.mp4"))
                live += list(store.storage_root.glob("media_*/*.mp4"))
                live += list(store.storage_root.glob("media_*/.media-output.mp4.publish"))
                live += list(store.storage_root.glob("media/media_*/*.mp4"))
                if outcome == "external_disk" and not external.exists() and any(path.stat().st_size for path in live):
                    external.write_bytes(b"x" * 65536)
                if external.exists():
                    live.append(external)
                # Model the cut's byte budgets and all live media allocations.
                consumed = sum(path.stat().st_size for path in live)
                disk_samples.append((initial - consumed, tuple(path.name for path in live)))
                return SimpleNamespace(free=initial - consumed)

            monkeypatch.setattr(psutil, "disk_usage", bounded_disk_usage)
        elif outcome == "changed_output":
            def change_before_publish(path, target, **options):
                with path.open("r+b") as handle:
                    first = handle.read(1)[0]
                    handle.seek(0)
                    handle.write(bytes([first ^ 1]))
                return copy_verified_cut_output(path, target, **options)

            monkeypatch.setattr("app.media_execution.bound_cut.copy_verified_cut_output", change_before_publish)
        submitted = client.post(url, json=request)
        if outcome in {"missing_receipt", "changed_tools", "changed_source", "no_access"}:
            assert submitted.status_code in {403, 409}, submitted.text
            assert not registered and not repo.job_records.list_job_runs("local", "task")
            assert not repo.execution_records.list_artifact_records("local", "task")
            assert repo.task_records.get_creation_task("local", "task").current_timeline_version_id == base.id
            return
        assert submitted.status_code == 202, submitted.text
        pending = submitted.json()
        executed = runtime.run_once()
        result = client.get(pending["result_url"]).json()
        if outcome in {"no_selection", "recorded_budget", "low_disk"}:
            assert not executed and not registered
            _, start = media.read(actor.workspace_id, result["job_id"])
            assert start["resources"]["kind"] == ("media_execution" if outcome == "no_selection" else BOUND_CUT_KIND)
            assert result["status"] == "queued"
            wait = result["resource_wait"]
            assert wait["context"] == "pending_request"
            assert wait["admitted"] is (None if outcome == "no_selection" else False)
            if outcome == "low_disk":
                disk = next(item for item in wait["checks"] if item["metric"] == "disk_free_bytes")
                assert disk["minimum"] == free + render.max_output_bytes and disk["passed"] is False
            assert not repo.execution_records.list_artifact_records("local", "task")
            assert repo.task_records.get_creation_task("local", "task").current_timeline_version_id == base.id
            assert runtime.scheduler.shutdown_released()
            return
        assert executed
        if outcome in {"changed_output", "external_disk"}:
            assert result["status"] == "failed"
            if outcome == "changed_output":
                assert result["failure_reason"] == "media_cut_output_changed"
                assert len(registered) == 3
            else:
                assert result["failure_reason"] == "media_cut_resource_refused"
                assert "other-job.mp4" in disk_samples[-1][1] and not registered
            assert runtime.scheduler.shutdown_released()
            assert not repo.execution_records.list_artifact_records("local", "task")
            assert repo.task_records.get_creation_task("local", "task").current_timeline_version_id == base.id
            assert not list((store.storage_root / "media").rglob("*.mp4"))
            return
        assert result["status"] == "succeeded", result
        if outcome == "boundary_disk":
            assert disk_samples[0][0] == initial
            assert any(free < initial and "source.mp4" in files for free, files in disk_samples)
            assert any(".media-output.mp4.publish" in files and "source.mp4" not in files for _, files in disk_samples)
        assert client.post(url, json=request).json() == result
        assert client.post(url, json={**request, "preparation_request_id": uuid4().hex}).status_code == 409
        assert len(registered) == 3 and all(grant == registered[0][0] for grant, _ in registered)
        assert runtime.scheduler.processes.children_released(tuple(child for _, child in registered)) is True
        assert runtime.scheduler.shutdown_released()
        artifact, = repo.execution_records.list_artifact_records("local", "task")
        assert reference in artifact.input_refs
        output = client.get(result["result"]["output_urls"][0])
        assert output.status_code == 200 and hashlib.sha256(output.content).hexdigest() == artifact.sha256
        version = repo.timeline_records.get_timeline_version("local", result["result"]["timeline_version_id"])
        assert version.parent_version_id == base.id
        event = repo.execution_records.get_task_event("local", f"event_{artifact.producer_job_id}_media_start")
        facts = event.payload["plan"]["metadata"][BOUND_CUT_FACT]
        assert facts["preparation"]["checkpoint"] == row["checkpoint_ref"] and facts["source"]["sha256"] == digest
        (tmp_path / "download.mp4").write_bytes(output.content)
        (tmp_path / "evidence.json").write_text(json.dumps({"preparation": facts["preparation"],
            "source": facts["source"], "request": request["request_id"], "outer_job": result["job_id"],
            "artifact": artifact.id, "artifact_sha256": artifact.sha256, "version": version.id,
            "registered_children": [child.pid for _, child in registered], "disk_samples": disk_samples}, indent=2))
    finally:
        assert runtime.stop(2)
