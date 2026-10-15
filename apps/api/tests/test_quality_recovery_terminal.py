"""One queue-recovery transaction boundary with a real late SQLite rejection."""
import json
import os
from pathlib import Path
import sqlite3

import pytest

from app.artifact_store import ArtifactStore
from app.creation.quality_inspection_use_case import QualityInspectionUseCase
from app.creation.output_observation_use_case import OutputObservationUseCase
from app.creation_execution import CreationExecutionActor
from app.production import CreationPlan, CreationTask, ProductionRepository
from app.quality_inspection import QualityCheckRepository
from app.task_audio import TaskAudio
from test_quality_resource_owner import build_metadata_runtime, fixture_budget, metadata_result, seed_source


def test_recovery_terminal_request_update_rolls_back_and_retries_same_receipt(tmp_path, monkeypatch):
    repository = ProductionRepository(tmp_path / "studio.sqlite", tmp_path / "library")
    plan = CreationPlan(id="plan", workspace_id="local", goal="测试", name="质检", target_count=1, language="zh-CN",
        aspect_ratio="16:9", duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0, material_rule_summary="", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint="isolated")
    task = CreationTask(id="task", workspace_id="local", plan_id="plan", name="质检", scenario="knowledge_explanation", stage="review")
    repository.confirm_creation_plan(plan, task, verified_owner_user_id="member")
    actor = CreationExecutionActor("local", "member")
    store = ArtifactStore(tmp_path / "artifacts")
    checks = QualityCheckRepository(repository.database_path)
    outputs = OutputObservationUseCase(repository, store, TaskAudio(repository, store, tmp_path), tmp_path, None)
    service = QualityInspectionUseCase(repository, checks, outputs, budget=fixture_budget(),
        verify_identity=lambda current: "ready" if current == actor else "unauthorized", temporary_root=tmp_path / "worker")
    seed_source(repository, store)
    source_before = service.source_metadata("local", "task")[2]
    calls = []

    def forbidden_worker(*args):
        calls.append(True)
        pytest.fail("已释放的声明回执恢复不能重跑 worker")

    runtime = build_metadata_runtime(tmp_path, service, forbidden_worker)
    try:
        check_id = service.start(runtime, actor, "local", "task", force=True)
        job_id = f"job_quality_{check_id}"
        event_id = f"event_{job_id}_quality_terminal"
        repository.claim_job_run("local", job_id)
        _, _, _, sealed = service.read("local", job_id)
        result = metadata_result(sealed)  # Explicit already-returned metadata port receipt.
        checks.store_worker_result("local", "task", check_id, result)
        service.budget = None

        def facts():
            with repository._connect() as conn:
                return {table: sorted([dict(row) for row in conn.execute(f"SELECT * FROM {table}")], key=lambda row: json.dumps(row, sort_keys=True))
                    for table in ("creation_task_quality_checks", "creation_task_quality_history", "job_runs", "task_events",
                                  "device_permit", "device_work_requests", "device_request_events")}

        before = facts()
        original_attempt = next(row["attempt_number"] for row in before["device_work_requests"] if row["owner_id"] == job_id)
        trace = []
        original_connect = service._connect

        def observe(statement):
            normalized = statement.strip().upper()
            for prefix, label in [("UPDATE CREATION_TASK_QUALITY_CHECKS", "current"),
                    ("UPDATE CREATION_TASK_QUALITY_HISTORY", "history"), ("UPDATE JOB_RUNS", "job"),
                    ("INSERT INTO TASK_EVENTS", "task_event"), ("UPDATE DEVICE_WORK_REQUESTS", "request"),
                    ("INSERT INTO DEVICE_REQUEST_EVENTS", "request_event"), ("ROLLBACK", "rollback")]:
                if normalized.startswith(prefix):
                    trace.append(label)
                    break

        def traced_connect():
            connection = original_connect()
            connection.set_trace_callback(observe)
            return connection

        monkeypatch.setattr(service, "_connect", traced_connect)
        with repository._connect() as conn:
            conn.execute("""CREATE TRIGGER c3_reject_last_request_update BEFORE UPDATE ON device_work_requests
                WHEN NEW.owner_type='quality_check' AND NEW.state='done'
                BEGIN SELECT RAISE(ABORT, 'c3_last_request_update_abort'); END""")
        try:
            with pytest.raises(sqlite3.IntegrityError, match="c3_last_request_update_abort"):
                service.recover(runtime.scheduler)
        finally:
            with repository._connect() as conn:
                conn.execute("DROP TRIGGER c3_reject_last_request_update")
        assert facts() == before
        ordered = [trace.index(label) for label in ("current", "history", "job", "task_event", "request", "rollback")]
        assert ordered == sorted(ordered)
        assert "request_event" not in trace
        assert checks.get_check("local", "task", check_id)["evidence"]["_worker_result"] == result
        assert calls == []
        service.recover(runtime.scheduler)
        assert repository.get_job_run("local", job_id).status == "succeeded"
        assert checks.get_check("local", "task", check_id)["result_status"] == "unavailable"
        assert checks.get("local", "task")["check_id"] == check_id
        assert repository.get_task_event("local", event_id).payload == {"check_id": check_id, "status": "succeeded"}
        with repository._connect() as conn:
            request = dict(conn.execute("SELECT * FROM device_work_requests WHERE owner_id=?", (job_id,)).fetchone())
            events = [dict(row) for row in conn.execute("SELECT * FROM device_request_events WHERE request_id=?", (request["id"],))]
            permit = dict(conn.execute("SELECT * FROM device_permit WHERE id=1").fetchone())
        assert (request["state"], request["wait_reason"], request["checkpoint_ref"]) == ("done", None, event_id)
        assert events == []
        assert request["attempt_number"] == original_attempt
        assert permit["state"] == "free" and permit["request_id"] is None
        completed = facts()
        service.recover(runtime.scheduler)
        assert facts() == completed and calls == []
        assert runtime.run_once() is False and calls == []
        assert service.source_metadata("local", "task")[2] == source_before
        evidence = os.environ.get("QUALITY_RESOURCE_EVIDENCE_DIR")
        if evidence:
            Path(evidence, "NORMAL_FACTS.json").write_text(json.dumps({"port": "declared released receipt; non-native",
                "check_id": check_id, "job_id": job_id, "event_id": event_id, "request_id": request["id"],
                "late_request_update_SQL_abort": "sqlite3.IntegrityError: c3_last_request_update_abort",
                "trace_order": ["current", "history", "job", "task_event", "request", "rollback"],
                "all_business_queue_permit_rows_rollback": True, "staged_receipt_retained": True,
                "request_state": "done", "checkpoint_ref": event_id, "request_events": 0,
                "job_status": "succeeded", "quality_status": "unavailable", "permit_state": "free",
                "worker_calls": 0, "second_recover_idempotent": True, "source_unchanged": True}, indent=2))
    finally:
        assert runtime.stop(1)
