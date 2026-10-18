"""Declared metadata ports only; this node is not native/resource calibration."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import pytest

from app.artifact_store import ArtifactStore
from app.creation.quality_execution import quality_check_registration
from app.creation.quality_inspection_use_case import QUALITY_KIND, QualityInspectionUseCase
from app.creation.output_observation_use_case import OutputObservationUseCase
from app.creation_execution import CreationExecutionActor
from app.device_runtime import build_device_runtime
from app.device_scheduler import ProcessIdentity, RecoveryBlocked, VerifiedSession
from app.host_resource_probes import HostResourceDecision
from app.montage import CreationTaskMontagePlan, MontageClip, creation_task_montage_plan_to_payload
from app.production import ArtifactRecord, CreationPlan, CreationTask, JobRun, ProductionRepository, TimelineVersion
from app.quality_checks import QualityReport, QualityUnavailable, quality_report_evidence
from app.quality_inspection import QualityCheckRepository
from app.quality_worker_runtime import QualityBudget, source_receipt
from app.task_audio import TaskAudio


def licensed_native_port(request):
    """The separate native gate supplies calibrated grant/process ports explicitly."""
    import pytest
    try:
        return request.getfixturevalue("licensed_quality_native")
    except pytest.FixtureLookupError:
        pytest.fail("质检 native fixture 尚未绑定独立标定预算与真实许可；本轮不能执行此矩阵。", pytrace=False)


class QualityCheckFixtureRepository(QualityCheckRepository):
    """Stamp a declared metadata source for the existing repository unit fixtures."""
    def finish(self, workspace_id, task_id, check_id, *, evidence=None, error=None, connection=None):
        if evidence is not None and error is None:
            record = self.get_check(workspace_id, task_id, check_id, connection=connection)
            source = _unit_source(record)
            evidence = {**evidence, "_source_receipt": source_receipt(source, check_id)}
        return super().finish(workspace_id, task_id, check_id, evidence=evidence, error=error, connection=connection)


def _unit_source(record):
    return {key: record[key] for key in ("workspace_id", "task_id", "timeline_version_id", "source_ref", "render_ref")} | {
        "artifact_id": "declared-unit-artifact", "size_bytes": 1,
        "file_identity": {"device": 1, "inode": 1, "size_bytes": 1, "mtime_ns": 1, "ctime_ns": 1}}


def unit_hash(path):
    raw = Path(path).read_bytes()
    assert len(raw) <= 1024  # These persistence fixtures are tiny non-media bytes.
    return hashlib.sha256(raw).hexdigest()


def unit_projection(record, plan, **kwargs):
    from app.quality_inspection import final_quality_check_payload
    return final_quality_check_payload(record, plan, current_source=_unit_source(record) if record else None, **kwargs)


def finish_report_fixture(repo, workspace_id, task_id, check_id, path, render_ref, *, inspector):
    """An explicit declared report port for persistence assertions, never native work."""
    from app.quality_checks import QualityCheckError
    try:
        result = inspector(path)
        if Path(path).exists() and unit_hash(path) != render_ref:
            raise QualityCheckError("检查期间成片内容已变化，请重新检查当前成片。")
        repo.finish(workspace_id, task_id, check_id, evidence=quality_report_evidence(result))
    except QualityCheckError as exc:
        repo.finish(workspace_id, task_id, check_id, error=str(exc))


def fixture_budget():
    # Synthetic port facts, with no executable or real host capacity claim.
    return QualityBudget(max_input_bytes=1024, max_pcm_bytes=2048, max_frame_json_bytes=4096,
        max_report_bytes=16384, max_stderr_bytes=1024, max_timeout_seconds=10., decoder_threads=1,
        max_process_rss_bytes=1024, max_process_threads=2, runtime_sample_seconds=.1,
        additional_memory_bytes=1024, additional_disk_bytes=1024, max_tool_bytes=1024,
        ffmpeg_path="/fixture-not-executable/ffmpeg", ffprobe_path="/fixture-not-executable/ffprobe",
        ffmpeg_sha256="1" * 64, ffprobe_sha256="2" * 64, worker_sha256="3" * 64)


class MetadataAccess:
    def verify_session(self, heartbeat, actor):
        return VerifiedSession(heartbeat.session_id, heartbeat.workspace_id, actor.member_id)

    def can_view_device_summary(self, actor):
        return actor.workspace_id == "local"


class MetadataResources:
    def __init__(self):
        self.admissions, self.releases = [], []

    def admit(self, request):
        self.admissions.append(request.id)
        return HostResourceDecision(True, dict(request.resources), {"declared_metadata_port": {}})

    def continue_unit(self, grant):
        return True

    def released(self, grant):
        self.releases.append(grant.request_id)
        return True


class MetadataProcesses:
    def __init__(self):
        self.recovery = "released"

    def current(self):
        return ProcessIdentity(os.getpid(), "metadata-fixture-start", "metadata-fixture-boot")

    def children_released(self, children):
        assert not children
        return True

    def recovery_state(self, holder, children):
        assert not children
        return self.recovery


def build_metadata_runtime(root, service, worker, *, resources=None, processes=None):
    return build_device_runtime(database_path=service.production.database_path,
        device_lock_path=(root / "device.lock").resolve(), device_access=MetadataAccess(),
        registrations={QUALITY_KIND: quality_check_registration(service, worker=worker)},
        resources=resources or MetadataResources(), processes=processes or MetadataProcesses(),
        poll_interval_seconds=.01)


def fixture_montage(ref):
    return CreationTaskMontagePlan(id="montage", workspace_id="local", task_id="task", script_id="script",
        script_title="测试", status="rendered", cues=[], duration_seconds=2, low_confidence_clip_count=0,
        output_path=ref, clips=[MontageClip("clip-1", "cue", "asset", "segment", "source", 0, 2, 0, 2, 1, [], False)])


def seed_source(repository, store, version="v1"):
    ref = f"artifacts/{version}.mp4"
    publication = store.publish_bytes(ref, f"non-media metadata fixture {version}".encode())
    producer = f"fixture-producer-{version}"
    repository.create_job_run(JobRun(id=producer, workspace_id="local", task_id="task", kind="creation_task_montage_render",
        idempotency_key=producer, input_digest=version))
    repository.create_artifact_record(ArtifactRecord(f"artifact-{version}", "local", "task", "rendered_video", ref,
        publication.sha256, publication.size_bytes, producer, []))
    payload = creation_task_montage_plan_to_payload(fixture_montage(ref))
    repository.save_creation_task_montage_plan("local", "task", "montage", payload)
    parent = repository.get_creation_task("local", "task").current_timeline_version_id
    sequence = repository.get_timeline_version("local", parent).sequence + 1 if parent else 1
    repository.create_timeline_version(TimelineVersion(id=version, workspace_id="local", task_id="task",
        parent_version_id=parent, sequence=sequence,
        snapshot={"montage": payload, "output_refs": [ref]}, created_by_operation_id=None))
    return publication


def metadata_result(request):
    report = QualityReport("fixture.mp4", (), (QualityUnavailable("clipping", "声明端口无 native 音频"),), 2, "declared-metadata-port")
    return {"evidence": quality_report_evidence(report), "receipt": source_receipt(request["source"], request["check_id"]),
        "worker_sha256": request["budget"]["worker_sha256"]}


def test_quality_owner_metadata_current_history_terminal_and_recovery(tmp_path):
    repository = ProductionRepository(tmp_path / "studio.sqlite", tmp_path / "library")
    plan = CreationPlan(id="plan", workspace_id="local", goal="测试", name="质检", target_count=1, language="zh-CN",
        aspect_ratio="16:9", duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0, material_rule_summary="", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint="isolated")
    task = CreationTask(id="task", workspace_id="local", plan_id="plan", name="质检", scenario="knowledge_explanation", stage="review")
    repository.confirm_creation_plan(plan, task, verified_owner_user_id="original-owner")
    repository.set_creation_task_visibility("local", "task", "shared")
    actor = CreationExecutionActor("local", "shared-reader")
    store = ArtifactStore(tmp_path / "artifacts")
    checks = QualityCheckRepository(repository.database_path)
    outputs = OutputObservationUseCase(repository, store, TaskAudio(repository, store, tmp_path), tmp_path, None)
    service = QualityInspectionUseCase(repository, checks, outputs, budget=fixture_budget(),
        verify_identity=lambda current: "ready" if current == actor else "unauthorized", temporary_root=tmp_path / "worker")
    seed_source(repository, store)
    calls, newer = [], []

    def worker(context, request, temporary_root):
        context.check()
        assert context.grant.owner_type == QUALITY_KIND
        assert repository.get_job_run("local", context.grant.owner_id).status == "running"
        calls.append(request["check_id"])
        if len(calls) == 1:
            seed_source(repository, store, "v2")
            newer.append(service.start(runtime, actor, "local", "task", force=True))
        return metadata_result(request)

    resources = MetadataResources()
    processes = MetadataProcesses()
    runtime = build_metadata_runtime(tmp_path, service, worker, resources=resources, processes=processes)
    try:
        old_id = service.start(runtime, actor, "local", "task", force=True)
        assert calls == []
        assert service.start(runtime, actor, "local", "task", force=True) == old_id
        with repository._connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM device_work_requests WHERE owner_type=?", (QUALITY_KIND,)).fetchone()[0] == 1
        assert runtime.run_once() is True
        assert checks.get_check("local", "task", old_id)["result_status"] == "unavailable"
        assert checks.get("local", "task")["check_id"] == newer[0]
        assert checks.get("local", "task")["check_state"] == "checking"
        assert runtime.run_once() is True
        current = service.projection("local", "task")
        assert current["check_id"] == newer[0] and current["result_status"] == "unavailable"
        assert current["stale"] is False and current["findings"] == []
        assert not any(key.startswith("_") for key in current)
        reopened = QualityCheckRepository(repository.database_path)
        for check_id in (old_id, newer[0]):
            job = repository.get_job_run("local", f"job_quality_{check_id}")
            assert job.status == "succeeded"
            terminal = repository.get_task_event("local", f"event_{job.id}_quality_terminal")
            assert terminal.payload == {"check_id": check_id, "status": "succeeded"}
            assert reopened.get_check("local", "task", check_id)["check_state"] == "done"
        assert calls == [old_id, newer[0]]
        assert len(resources.admissions) == 4 and len(resources.releases) >= 2
        released_result = service.start(runtime, actor, "local", "task", force=True)
        grant = runtime.scheduler.pick_and_grant()
        assert grant is not None and grant.owner_id == f"job_quality_{released_result}"
        job = repository.claim_job_run("local", grant.owner_id)
        _, _, record, request = service.read("local", job.id)
        # A declared already-returned worker receipt, not the separate late-SQL gate.
        checks.store_worker_result("local", "task", record["check_id"], metadata_result(request))
        seed_source(repository, store, "v3")
        interrupted = service.start(runtime, actor, "local", "task", force=True)
        assert interrupted != released_result
        assert runtime.stop(1)
        runtime = build_metadata_runtime(tmp_path, service, worker, resources=resources, processes=processes)

        def persisted_facts():
            with repository._connect() as conn:
                return {table: sorted([dict(row) for row in conn.execute(f"SELECT * FROM {table}")], key=lambda row: json.dumps(row, sort_keys=True))
                    for table in ("creation_task_quality_checks", "creation_task_quality_history", "job_runs", "task_events",
                                  "device_permit", "device_work_requests", "device_request_events")}

        before = persisted_facts()
        admissions_before = len(resources.admissions)
        for state in ("unknown", "active"):
            processes.recovery = state
            with pytest.raises(RecoveryBlocked):
                service.recover(runtime.scheduler)
            assert persisted_facts() == before
            assert len(calls) == 2 and len(resources.admissions) == admissions_before
        # Metadata settlement of the sealed old receipt does not borrow a new profile.
        service.budget = None
        processes.recovery = "released"
        service.recover(runtime.scheduler)
        assert repository.get_job_run("local", f"job_quality_{released_result}").status == "succeeded"
        assert reopened.get_check("local", "task", released_result)["result_status"] == "unavailable"
        assert repository.get_task_event("local", f"event_job_quality_{released_result}_quality_terminal").payload == {
            "check_id": released_result, "status": "succeeded"}
        assert reopened.get("local", "task")["check_id"] == interrupted
        assert reopened.get("local", "task")["check_state"] == "failed"
        assert "服务中断" in service.projection("local", "task")["error"]
        assert repository.get_job_run("local", f"job_quality_{interrupted}").status == "cancelled"
        assert len(calls) == 2
        evidence_root = os.environ.get("QUALITY_RESOURCE_EVIDENCE_DIR")
        if evidence_root:
            Path(evidence_root, "NORMAL_FACTS.json").write_text(json.dumps({"port": "non-native metadata only",
                "checks": [old_id, newer[0], released_result, interrupted], "worker_calls": calls, "shared_reader": actor.member_id,
                "terminal_status": "unavailable", "recovery_status": "failed", "runtime_run_once_calls": 2,
                "unknown_active_full_DB_unchanged": True, "released_receipt_resumed_without_worker": True,
                "recovery_current_profile": None, "reconciliation_extra_admissions": len(resources.admissions) - admissions_before}, indent=2))
    finally:
        assert runtime.stop(1)
