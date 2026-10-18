"""Owner commit checks read selected SQL revisions without remote/file work."""
from datetime import datetime, timedelta, timezone
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app.creation_execution import CreationExecutionActor, CreationJobOwnerVerifier
from app.production import JobRun, MaterialAsset, ProductionRepository, TaskEvent
from tests.test_creation_execution import _seed


def _forbidden(*args, **kwargs):
    raise AssertionError("commit check performed remote or filesystem work")


def _local(repo, owner, request, observed, monkeypatch):
    with repo._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        def read_only(action, *args):
            return sqlite3.SQLITE_DENY if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE} else sqlite3.SQLITE_OK
        conn.set_authorizer(read_only)
        with monkeypatch.context() as guard:
            guard.setattr(owner, "check_current", _forbidden)
            guard.setattr(Path, "stat", _forbidden)
            guard.setattr(Path, "read_bytes", _forbidden)
            verdict = owner.check_local(request, conn, observed)
        conn.rollback()
    return verdict


def _asset(repo, workspace, name):
    asset = MaterialAsset(name, workspace, name, str(repo.video_library_path / name), 10,
                          datetime.now(timezone.utc))
    repo._save_asset(asset)
    return asset


def test_creation_owner_ignores_unrelated_task_text_but_refuses_changed_source_revision(tmp_path, monkeypatch):
    repo, task_id, script_id, digest = _seed(tmp_path, voice=False)
    job = repo.create_job_run(JobRun(id="job", workspace_id="workspace_a", task_id=task_id,
        kind="creation_task_narration", idempotency_key="job", input_digest=digest))
    repo.append_task_event(TaskEvent(id="event_job_start", workspace_id=job.workspace_id,
        task_id=task_id, job_id=job.id, event_type="creation_execution_started",
        payload={"script_id": script_id, "member_id": "member"}))
    asset = _asset(repo, job.workspace_id, "source.mp4")
    owner = CreationJobOwnerVerifier(repo)
    request = SimpleNamespace(workspace_id=job.workspace_id, owner_id=job.id, unit_key="montage_plan:fixed")
    with repo._connect() as conn:
        observed = owner.capture_local(request, conn)
        conn.execute("UPDATE creation_tasks SET payload=json_set(payload,'$.name','new title') WHERE id=?", (task_id,))
    assert _local(repo, owner, request, observed, monkeypatch) == "ready"
    with repo._connect() as conn:
        conn.execute("UPDATE material_assets SET updated_at='new revision' WHERE id=?", (asset.id,))
    assert _local(repo, owner, request, observed, monkeypatch) == "stale"


def test_profile_preview_local_check_reuses_store_and_refuses_revocation_without_io(tmp_path, monkeypatch):
    from tests.test_profile_api_contract import setup_preview
    repo = ProductionRepository(tmp_path / "preview.sqlite", tmp_path / "library")
    runtime, _, _, queued = setup_preview(repo, tmp_path)
    owner = runtime.registrations["media_preview"].owner
    request = runtime.scheduler._get(queued.device_request_id)
    with repo._connect() as conn:
        assert owner.check_current(request, conn) == "ready"
        observed = owner.capture_local(request, conn)
    assert _local(repo, owner, request, observed, monkeypatch) == "ready"
    with repo._connect() as conn:
        conn.execute("UPDATE versioned_voice_profiles SET payload=json_set(payload,'$.use_state','excluded') WHERE workspace_id='ws' AND id='voice'")
    assert _local(repo, owner, request, observed, monkeypatch) == "unauthorized"
    runtime.stop(0)


def test_task_media_local_check_fences_its_sources_and_leaves_unrelated_assets_out(tmp_path, monkeypatch):
    from app.task_audio import TaskMediaJobs
    from app.device_scheduler import SubmissionIntent, SchedulePolicy
    repo, task_id, _, _ = _seed(tmp_path, voice=False)
    source = _asset(repo, "workspace_a", "source.wav")
    unrelated = _asset(repo, "workspace_a", "unrelated.wav")
    actor = CreationExecutionActor("workspace_a", "member")
    media = TaskMediaJobs(repo, facts=lambda *args: ({"source": source.path}, {"kind": "fixture"}),
                          verify_identity=lambda actor: "ready")
    runtime = SimpleNamespace(scheduler=SimpleNamespace(submit=lambda *args: None), wake=lambda: None)
    job = media.submit(runtime, actor, task_id, "audio_waveform", {}, "waveform")
    work = media.verify_submission(SubmissionIntent(job.workspace_id, "creation_job", job.id,
        job.input_digest, f"task_media:{job.input_digest}", SchedulePolicy()), actor)
    request = SimpleNamespace(**vars(work), state="waiting", id="request", last_grant_generation=None)
    with repo._connect() as conn:
        assert media.check_current(request, conn) == "ready"
        observed = media.capture_local(request, conn)
        conn.execute("UPDATE material_assets SET updated_at='unrelated edit' WHERE id=?", (unrelated.id,))
    assert _local(repo, media, request, observed, monkeypatch) == "ready"
    with repo._connect() as conn:
        conn.execute("UPDATE material_assets SET updated_at='source changed' WHERE id=?", (source.id,))
    assert _local(repo, media, request, observed, monkeypatch) == "stale"


@pytest.mark.parametrize("rollback", [False, True])
def test_task_media_retry_reopens_owner_and_device_attempt_in_one_transaction(tmp_path, monkeypatch, rollback):
    from app.task_audio import TaskMediaJobs
    from app.device_runtime import OwnerRegistration, OwnerRouter
    from app.device_scheduler import DeviceScheduler, SubmissionIntent, SchedulePolicy
    from tests.test_device_scheduler import Resources, Processes
    repo, task_id, _, _ = _seed(tmp_path, voice=False)
    actor = CreationExecutionActor("workspace_a", "member")
    media = TaskMediaJobs(repo, facts=lambda *args: ({"base_version_id": None}, {"kind": "fixture"}),
                          verify_identity=lambda actor: "ready")
    owner = CreationJobOwnerVerifier(repo, task_media=media)
    scheduler = DeviceScheduler(repo.database_path, tmp_path / "device.lock",
        owners=OwnerRouter({"creation_job": OwnerRegistration(owner, None)}, SimpleNamespace()),
        resources=Resources(), processes=Processes(), activity_source_reliable=True)
    job = media.submit(SimpleNamespace(scheduler=scheduler, wake=lambda: None), actor,
        task_id, "audio_waveform", {}, "waveform")
    grant = scheduler.pick_and_grant()
    assert grant is not None
    scheduler.fail_unit(grant, reason="fixture_failed", failure_write=lambda conn, current:
        conn.execute("UPDATE job_runs SET status='failed' WHERE id=?", (job.id,)))
    if rollback:
        original = scheduler._record_event
        def record(conn, row, event, *args):
            if event == "retried":
                raise RuntimeError("retry audit failed")
            return original(conn, row, event, *args)
        monkeypatch.setattr(scheduler, "_record_event", record)
        with pytest.raises(RuntimeError, match="retry audit failed"):
            scheduler.retry_existing(grant.request_id, actor, reason="failed")
    else:
        scheduler.retry_existing(grant.request_id, actor, reason="failed")
    assert repo.get_job_run("workspace_a", job.id).status == ("failed" if rollback else "interrupted")
    request = scheduler._get(grant.request_id)
    assert (request.state, request.attempt_number) == (("failed", 1) if rollback else ("waiting", 2))
    scheduler.close()


def test_material_local_check_ignores_scan_heartbeat_and_refuses_changed_source(tmp_path, monkeypatch):
    from app.material_ingestion import MaterialHashOwnerVerifier
    repo = ProductionRepository(tmp_path / "material.sqlite", tmp_path / "library")
    asset = _asset(repo, "workspace", "source.mp4")
    with repo._connect() as conn:
        conn.execute("INSERT INTO material_ingestion_binding(id,workspace_id,library_path) VALUES(1,?,?)", (asset.workspace_id, str(repo.video_library_path)))
        conn.execute("""INSERT INTO material_ingestion_files(workspace_id,file_path,size_bytes,mtime_ns,observed_at,state,provisional_revision)
            VALUES(?,?,10,1,1,'waiting','revision')""", (asset.workspace_id, asset.path))
    owner = MaterialHashOwnerVerifier(repo)
    request = SimpleNamespace(workspace_id=asset.workspace_id, owner_id=asset.id, input_digest="revision")
    with repo._connect() as conn:
        observed = owner.capture_local(request, conn)
        conn.execute("UPDATE material_ingestion_files SET observed_at=2")
    assert _local(repo, owner, request, observed, monkeypatch) == "ready"
    with repo._connect() as conn:
        conn.execute("UPDATE material_ingestion_files SET provisional_revision='changed'")
    assert _local(repo, owner, request, observed, monkeypatch) == "stale"


def test_retrieval_local_check_never_reads_provider_files_or_remote_identity(tmp_path, monkeypatch):
    from tests.test_material_semantic_retrieval import setup, _cache
    case = setup.__wrapped__(tmp_path)
    query_id = _cache(case, "factory")
    owner = case.service
    row = owner.assets.read_semantic_query("workspace", query_id)
    actor, digest, unit, resources = owner._query_facts(row)
    request = SimpleNamespace(workspace_id="workspace", owner_id=query_id, owner_type="material_retrieval",
        member_id=actor.member_id, input_digest=digest, unit_key=unit, resources=resources,
        source_refs=(), work_class="preview_dependency")
    with case.repo._connect() as conn:
        assert owner.check_current(request, conn) == "ready"
        observed = owner.capture_local(request, conn)
    monkeypatch.setattr(owner, "verify_identity", _forbidden)
    assert _local(case.repo, owner, request, observed, monkeypatch) == "ready"
    with case.repo._connect() as conn:
        conn.execute("UPDATE material_semantic_queries SET query_text='changed' WHERE id=?", (query_id,))
    assert _local(case.repo, owner, request, observed, monkeypatch) == "stale"


def test_publish_local_check_never_reopens_output_and_refuses_account_change(tmp_path, monkeypatch):
    from tests.test_platform_publish import _persistent_runtime_fixture, _payload
    client, repo, runtime, _, auth = _persistent_runtime_fixture(tmp_path)
    response = client.post("/api/publishing/tasks/task-1/publish", json=_payload())
    assert response.status_code == 202
    owner = runtime.registrations["platform_publish"].owner
    with repo._connect() as conn:
        request_id = conn.execute("SELECT id FROM device_work_requests").fetchone()[0]
        request = runtime.scheduler._get(request_id)
        assert owner.check_current(request, conn) == "ready"
        observed = owner.capture_local(request, conn)
    monkeypatch.setattr(owner, "publishable_output_for", _forbidden)
    assert _local(repo, owner, request, observed, monkeypatch) == "ready"
    with repo._connect() as conn:
        conn.execute("UPDATE platform_browser_accounts SET account_ref='changed'")
    assert _local(repo, owner, request, observed, monkeypatch) == "unauthorized"
    runtime.stop(0)


def test_quality_local_check_keeps_fixed_history_but_refuses_task_access_change(tmp_path, monkeypatch):
    from app.artifact_store import ArtifactStore
    from app.creation.quality_inspection_use_case import QualityInspectionUseCase, QUALITY_KIND
    from app.creation.quality_execution import QualityCheckOwnerVerifier
    from app.creation.output_observation_use_case import OutputObservationUseCase
    from app.quality_inspection import QualityCheckRepository
    from app.production import CreationPlan, CreationTask
    from app.task_audio import TaskAudio
    from tests.test_quality_resource_owner import fixture_budget, seed_source, build_metadata_runtime
    repo = ProductionRepository(tmp_path / "quality.sqlite", tmp_path / "library")
    plan = CreationPlan(id="plan", workspace_id="local", goal="fixture", name="fixture", target_count=1,
        language="zh", aspect_ratio="16:9", duration_target_seconds=2, knowledge_references=[],
        source_references=[], selected_voice=None, pronunciation_references=[], approved_asset_count=0,
        approved_clip_count=0, material_rule_summary="", model_snapshot=[], confirmation_points=[], blockers=[], preflight_fingerprint="fixture")
    repo.confirm_creation_plan(plan, CreationTask(id="task", workspace_id="local", plan_id="plan", name="fixture", scenario="fixture", stage="review"), verified_owner_user_id="member")
    store = ArtifactStore(tmp_path / "artifacts")
    outputs = OutputObservationUseCase(repo, store, TaskAudio(repo, store, tmp_path), tmp_path, None)
    service = QualityInspectionUseCase(repo, QualityCheckRepository(repo.database_path), outputs,
        budget=fixture_budget(), verify_identity=lambda actor: "ready", temporary_root=tmp_path / "worker")
    seed_source(repo, store)
    runtime = build_metadata_runtime(tmp_path, service, _forbidden)
    service.start(runtime, CreationExecutionActor("local", "member"), "local", "task")
    owner = runtime.registrations[QUALITY_KIND].owner
    with repo._connect() as conn:
        request_id = conn.execute("SELECT id FROM device_work_requests").fetchone()[0]
        request = runtime.scheduler._get(request_id)
        assert owner.check_current(request, conn) == "ready"
        observed = owner.capture_local(request, conn)
    monkeypatch.setattr(service, "verify_identity", _forbidden)
    monkeypatch.setattr(service, "verify_source", _forbidden)
    seed_source(repo, store, "v2")
    assert _local(repo, owner, request, observed, monkeypatch) == "ready"
    with repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=json_set(payload,'$.owner_user_id','other') WHERE id='task'")
    assert _local(repo, owner, request, observed, monkeypatch) == "unauthorized"
    runtime.stop(0)


def test_cover_local_check_uses_original_binding_without_reading_source_or_receipt(tmp_path, monkeypatch):
    from app.artifact_store import ArtifactStore
    from app.creation.cover_use_case import CoverCommand, CoverUseCase, COVER_JOB_KIND, COVER_OPERATION, file_identity
    from app.production import ArtifactRecord, EditIntent, EditProposal, TimelineVersion
    from app.host_resource_probes import CoverBudget
    repo, task_id, _, _ = _seed(tmp_path, voice=False)
    workspace = "workspace_a"
    actor = CreationExecutionActor(workspace, "member")
    store = ArtifactStore(tmp_path / "artifacts")
    ref = "artifacts/source.mp4"
    published = store.publish_bytes(ref, b"declared metadata source, never decoded")
    operation = {"base_version_id": "base", "source_video_ref": ref,
                 "title": "fixture", "frame_time_seconds": 0., "title_position": "bottom"}
    command = CoverCommand(workspace, task_id, "proposal", "request",
        json.dumps(actor.identity_payload(), ensure_ascii=False, sort_keys=True),
        json.dumps(operation, ensure_ascii=False, sort_keys=True), published.sha256,
        published.size_bytes, file_identity(store.resolve_file(ref)))
    job = repo.create_job_run(JobRun(id="cover-job", workspace_id=workspace, task_id=task_id,
        kind=COVER_JOB_KIND, idempotency_key=command.idempotency_key, input_digest=command.input_digest))
    repo.create_artifact_record(ArtifactRecord("source-artifact", workspace, task_id, "rendered_video",
        ref, published.sha256, published.size_bytes, job.id, []))
    repo.create_timeline_version(TimelineVersion("base", workspace, task_id, None, 1,
        {"output_refs": [ref]}, None))
    repo.create_edit_intent(EditIntent("intent", workspace, task_id, "cover", "review", [], None,
        "cover", {}, {}, 1., "proposed"))
    repo.create_edit_proposal(EditProposal("proposal", workspace, task_id, "intent", "cover",
        ["timeline:base"], {"operation_type": COVER_OPERATION, "payload": operation}, [], {}, []))
    owner = CoverUseCase(repo, store, None, verify_identity=lambda actor: "ready", output_root=tmp_path,
        budget=CoverBudget(1024, 4096, 32768, 1024, 1024, 1, 10., 1048576, 4, .01, 1048576, 65536))
    resources = owner.resources(command)
    payload = {"command": asdict(command), "resources": resources, **actor.identity_payload(),
               "deadline_utc": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
    repo.append_task_event(TaskEvent(id="event_cover-job_cover_start", workspace_id=workspace,
        task_id=task_id, event_type="cover_generation_submitted", payload=payload, job_id=job.id))
    request = SimpleNamespace(workspace_id=workspace, owner_id=job.id, owner_type="creation_job",
        member_id=actor.member_id, input_digest=command.input_digest, unit_key=command.unit_key,
        work_class="preview_dependency", resources=resources, source_refs=(), state="waiting",
        id="request", last_grant_generation=None)
    with repo._connect() as conn:
        assert owner.check_current(request, conn) == "ready"
        observed = owner.capture_local(request, conn)
    monkeypatch.setattr(owner, "verify_identity", _forbidden)
    monkeypatch.setattr(owner, "read_staged", _forbidden)
    assert _local(repo, owner, request, observed, monkeypatch) == "ready"
    with repo._connect() as conn:
        conn.execute("UPDATE edit_proposals SET status='confirmed',updated_at='changed' WHERE id='proposal'")
    assert _local(repo, owner, request, observed, monkeypatch) == "stale"
