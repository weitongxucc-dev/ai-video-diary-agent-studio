"""Opt-in replay of a retained isolated preparation, without recalibrating it."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app import main
from app.artifact_store import ArtifactStore
from app.creation.contracts import TimelineVersion
from app.creation.task_authorization import TaskAuthorizationUseCase
from app.creation_execution import CreationExecutionActor
from app.device_resource_configuration import current_device_identity
from app.device_resource_preparation import DeviceResourcePreparation
from app.host_resource_probes import (
    HostBudget, HostProcessProbe, HostResourceDecision, HostResourceProbe, RuntimeCpuPolicy, SceneRuntimeLimits,
)
from app.local_members import LocalMemberService
from app.material_ingestion import MaterialIngestionService
from app.media_execution import EditPlan, TaskMediaExecutionDelegate, build_media_execution_entrypoint
from app.media_execution.bound_cut import BOUND_CUT_FACT, BOUND_CUT_KIND, BoundMp4CutUseCase
from app.production import MaterialSegment
from test_host_resource_probes import _render_budget
from test_material_ingestion import hash_scheduler
from test_task_media_grants import case


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


@pytest.mark.parametrize("outcome", ["published", "missing_receipt", "changed_tools", "changed_source", "no_access"])
def test_cut_replays_an_existing_completed_preparation(tmp_path, monkeypatch, retained_preparation, outcome):
    preparation_root, prep_request, existing_source = retained_preparation
    render = _render_budget()

    class IsolatedCutResources(HostResourceProbe):
        def admit(self, request):
            assert request.resources["kind"] == BOUND_CUT_KIND
            real = super().admit(request)
            assert real.verdict is None and real.reason == "unsupported_resource_kind"
            return HostResourceDecision(True, dict(request.resources), {})

        def continue_unit(self, grant):
            return self._grant_resources(grant)["kind"] == BOUND_CUT_KIND

        def scene_runtime_limits(self, grant, *, purpose=None):
            assert self.continue_unit(grant)
            return SceneRuntimeLimits(384 * 1024**2, 16, 0, None, self.budget.sample_seconds,
                                      self.memory_monitor, cpu_policy=cpu_policy)

        def released(self, grant):
            return self.continue_unit(grant)

    host = IsolatedCutResources(tmp_path / "media.sqlite", disk_paths=(tmp_path,),
                                budget=HostBudget(100, 0, 2**63, 0, 2**63, 8 * 1024**2, .02))
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
        write_paths={"database": tmp_path, "output": tmp_path / "artifacts", "temporary": tmp_path / "scratch"},
        verify_identity=media.verify_identity)
    witness = preparation.read_mp4_cut_measurement(database_path=repo.database_path, request_id=prep_request, actor=actor)
    assert witness["measurement"]["source"]["sha256"] == digest and witness["checkpoint"] == row["checkpoint_ref"]
    cpu_policy = RuntimeCpuPolicy(**witness["measurement"]["cpu_policy"])
    cut = BoundMp4CutUseCase(repo, preparation, auth)
    store = ArtifactStore(tmp_path / "artifacts")
    entrypoint = build_media_execution_entrypoint(repository=repo, output_root=tmp_path / "artifacts",
        connection_factory=repo.connection_factory, artifact_store=store)
    media.media_execution_delegate = TaskMediaExecutionDelegate(entrypoint, bounded_cut=cut)
    monkeypatch.setattr(main, "production_repo", repo)
    monkeypatch.setattr(main, "artifact_store", store)
    monkeypatch.setattr(main, "local_members_service", members)
    monkeypatch.setattr(main, "settings", replace(main.settings, database_path=str(repo.database_path),
        video_output_path=str(tmp_path / "artifacts"), video_inbox_path=str(repo.video_library_path),
        temporary_root_path=str(tmp_path / "scratch")))
    monkeypatch.setattr(main, "_device_operation_budget", lambda key: render if key == "cpu_render" else None)

    def seal(ws, task, operation, arguments, actor):
        facts, resources = main._task_media_facts(ws, task, operation, arguments, actor)
        binding = cut.seal(EditPlan.from_value(arguments["edit_plan"]), actor, prep_request)
        return {**facts, BOUND_CUT_FACT: binding}, {**resources, "kind": BOUND_CUT_KIND,
            "threads": 1, "timeout_seconds": 10, "max_output_bytes": 8 * 1024**2}

    media.facts = seal
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
               "start_seconds": .25, "end_seconds": 1.25}
    url = "/api/creation-tasks/task/media-jobs"
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
        submitted = client.post(url, json=request)
        if outcome != "published":
            assert submitted.status_code in {403, 409}, submitted.text
            assert not registered and not repo.job_records.list_job_runs("local", "task")
            assert not repo.execution_records.list_artifact_records("local", "task")
            assert repo.task_records.get_creation_task("local", "task").current_timeline_version_id == base.id
            return
        assert submitted.status_code == 202, submitted.text
        pending = submitted.json()
        assert runtime.run_once()
        result = client.get(pending["result_url"]).json()
        assert result["status"] == "succeeded", result
        assert client.post(url, json=request).json() == result
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
            "registered_children": [child.pid for _, child in registered]}, indent=2))
    finally:
        assert runtime.stop(2)
