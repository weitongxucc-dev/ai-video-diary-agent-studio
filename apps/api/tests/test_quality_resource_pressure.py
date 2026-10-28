"""One declared resource-pressure port; no native or host calibration claim."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from app.artifact_store import ArtifactStore
from app.creation.quality_inspection_use_case import QualityInspectionUseCase
from app.creation.output_observation_use_case import OutputObservationUseCase
from app.creation_execution import CreationExecutionActor
from app.host_resource_probes import HostResourceDecision, HostResourceRefusal
from app.production import CreationPlan, ProductionRepository
from app.creation.contracts import CreationTask
from app.quality_inspection import QualityCheckRepository
from app.task_audio import TaskAudio
from test_quality_resource_owner import MetadataResources, build_metadata_runtime, fixture_budget, seed_source


def test_quality_pressure_defers_to_future_without_terminal_result(tmp_path):
    repository = ProductionRepository(tmp_path / "studio.sqlite", tmp_path / "library")
    plan = CreationPlan(id="plan", workspace_id="local", goal="测试", name="质检", target_count=1, language="zh-CN",
        aspect_ratio="16:9", duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0, material_rule_summary="", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint="isolated")
    task = CreationTask(id="task", workspace_id="local", plan_id="plan", name="质检", scenario="knowledge_explanation", stage="review")
    repository.confirm_creation_plan(plan, task, verified_owner_user_id="original-owner")
    actor = CreationExecutionActor("local", "original-owner")
    store = ArtifactStore(tmp_path / "artifacts")
    checks = QualityCheckRepository(repository.database_path)
    outputs = OutputObservationUseCase(repository, store, TaskAudio(repository, store, tmp_path), tmp_path, None)
    service = QualityInspectionUseCase(repository, checks, outputs, budget=fixture_budget(),
        verify_identity=lambda current: "ready" if current == actor else "unauthorized", temporary_root=tmp_path / "worker")
    seed_source(repository, store)
    source_before = service.source_metadata("local", "task")[2]
    calls = []

    def pressure_worker(context, request, temporary_root):
        context.check()
        calls.append(request["check_id"])
        # Explicit synthetic refusal facts through the real refusal classifier.
        refusal = HostResourceRefusal(HostResourceDecision(False, dict(request["resources"]),
            {"available_memory_bytes": {"passed": False, "actual": 1, "minimum": 2}}))
        assert refusal.recoverable_pressure is True
        raise refusal

    resources = MetadataResources()
    runtime = build_metadata_runtime(tmp_path, service, pressure_worker, resources=resources)
    try:
        check_id = service.start(runtime, actor, "local", "task", force=True)
        job_id = f"job_quality_{check_id}"
        before = datetime.now(timezone.utc)
        assert runtime.run_once() is True
        after = datetime.now(timezone.utc)
        with repository._connect() as conn:
            request = dict(conn.execute("SELECT * FROM device_work_requests WHERE owner_id=?", (job_id,)).fetchone())
            permit = dict(conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone())
        retry_at = datetime.fromisoformat(request["retry_not_before_utc"])
        assert request["state"] == "blocked" and request["wait_reason"] == "resource_pressure"
        assert before + timedelta(seconds=30) <= retry_at <= after + timedelta(seconds=30)
        assert permit["state"] == "free" and permit["request_id"] is None
        assert repository.get_job_run("local", job_id).status == "interrupted"
        assert checks.get("local", "task")["check_state"] == "checking"
        assert service.projection("local", "task")["result_status"] is None
        with pytest.raises(KeyError):
            repository.execution_records.get_task_event("local", f"event_{job_id}_quality_terminal")
        assert service.source_metadata("local", "task")[2] == source_before
        assert calls == [check_id] and runtime.last_error is None
        admissions = len(resources.admissions)
        assert runtime.run_once() is False  # Original scheduler enforces the future gate.
        assert calls == [check_id] and len(resources.admissions) == admissions
        with repository._connect() as conn:
            gated = conn.execute("SELECT state,wait_reason FROM device_work_requests WHERE owner_id=?", (job_id,)).fetchone()
        assert (gated["state"], gated["wait_reason"]) == ("blocked", "retry_not_before")
        assert service.source_metadata("local", "task")[2] == source_before
        evidence = os.environ.get("QUALITY_RESOURCE_EVIDENCE_DIR")
        if evidence:
            Path(evidence, "NORMAL_FACTS.json").write_text(json.dumps({"port": "declared pressure; non-native",
                "check_id": check_id, "request_state": request["state"], "wait_reason": request["wait_reason"],
                "wait_reason_after_future_gate": gated["wait_reason"],
                "retry_not_before_utc": retry_at.isoformat(), "retry_interval_seconds": 30,
                "job_state": "interrupted", "check_state": "checking", "permit_state": "free",
                "worker_calls": len(calls), "source_unchanged": True, "future_gate_blocks_second_dispatch": True}, indent=2))
    finally:
        assert runtime.stop(1)
