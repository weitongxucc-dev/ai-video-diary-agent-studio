"""One queue-recovery transaction boundary with a real late SQLite rejection."""
import json
import os
from pathlib import Path
import sqlite3
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from app.device_scheduler import RecoveryBlocked, SourceRef, VerifiedWork

import pytest

from app.artifact_store import ArtifactStore
from app.creation.quality_inspection_use_case import QualityInspectionUseCase
from app.creation.output_observation_use_case import OutputObservationUseCase
from app.creation_execution import CreationExecutionActor
from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import CreationTask
from app.quality_inspection import QualityCheckRepository
from app.task_audio import TaskAudio
from test_quality_resource_owner import build_metadata_runtime, fixture_budget, metadata_result, seed_source, stage_metadata_prefix
from test_device_scheduler import _sqlite_connection_facts, _sqlite_table_facts, _trace_sqlite_commits


@pytest.mark.parametrize("hold_until_timeout", [False, True], ids=["release_within_wait", "timeout_then_resume"])
def test_production_snapshot_contends_with_original_quality_recovery(tmp_path, monkeypatch, hold_until_timeout):
    from test_creation_execution import _seed

    # An independent confirmed task holds a real Production snapshot in the same database.
    repository, snapshot_task_id, _, _ = _seed(tmp_path, workspace_id="local", voice=False)
    plan = CreationPlan(id="plan", workspace_id="local", goal="测试", name="质检", target_count=1, language="zh-CN",
        aspect_ratio="16:9", duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0, material_rule_summary="", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint="isolated")
    task = CreationTask(id="task", workspace_id="local", plan_id="plan", name="质检", scenario="knowledge_explanation", stage="review")
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id="member")
    actor = CreationExecutionActor("local", "member")
    store = ArtifactStore(tmp_path / "artifacts")
    checks = QualityCheckRepository(repository.database_path)
    outputs = OutputObservationUseCase(repository, store, TaskAudio(repository, store, tmp_path), tmp_path, None)
    service = QualityInspectionUseCase(repository, checks, outputs, budget=fixture_budget(),
        verify_identity=lambda current: "ready" if current == actor else "unauthorized", temporary_root=tmp_path / "worker")
    seed_source(repository, store)

    def forbidden_worker(*args):
        pytest.fail("lock contention must reuse the sealed receipt without worker execution")

    runtime = build_metadata_runtime(tmp_path, service, forbidden_worker)
    try:
        check_id = service.start(runtime, actor, "local", "task", force=True)
        job_id = f"job_quality_{check_id}"
        event_id = f"event_{job_id}_quality_terminal"
        sealed = stage_metadata_prefix(runtime, service, actor, check_id)
        result = metadata_result(sealed)
        checks.store_worker_result("local", "task", check_id, result)
        service.budget = None
        tables = ("creation_task_quality_checks", "creation_task_quality_history", "job_runs", "task_events",
                  "device_permit", "device_work_requests", "device_request_events")
        before = _sqlite_table_facts(repository, tables)
        original = next(row for row in before["device_work_requests"]
                        if row["owner_id"] == job_id and row["unit_key"].endswith(":finalize"))
        attempted, connections, statements = _trace_sqlite_commits(monkeypatch, service)
        writer_ready, reader_ready = Event(), Event()
        complete_result = service.complete_result

        def gated_complete_result(conn, *args, **kwargs):
            # Reconcile has finished. This is the original terminal owner transaction.
            assert conn.in_transaction
            writer_ready.set()
            assert reader_ready.wait(10), "Production snapshot did not acquire its read lock"
            return complete_result(conn, *args, **kwargs)

        monkeypatch.setattr(service, "complete_result", gated_complete_result)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(service.recover, runtime.scheduler)
            try:
                assert writer_ready.wait(10), "recovery did not reach its terminal owner transaction"
                with repository.creation_execution_read() as reader:
                    snapshot = repository.read_creation_execution_snapshot("local", snapshot_task_id, connection=reader)
                    reader_facts = _sqlite_connection_facts(reader)
                    assert snapshot.task.id == snapshot_task_id and reader.in_transaction
                    started = monotonic()
                    reader_ready.set()
                    assert attempted.wait(10), "QC writer did not reach its COMMIT attempt"
                    assert not pending.done()
                    if hold_until_timeout:
                        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                            pending.result(timeout=reader_facts["busy_timeout_ms"] / 1000 + 5)
                        assert "ROLLBACK" in statements
                        assert _sqlite_table_facts(repository, tables) == before
                        assert checks.get_check("local", "task", check_id)["evidence"]["_worker_result"] == result
                        assert repository.get_job_run("local", job_id).status == "running"
                elapsed = monotonic() - started
                if not hold_until_timeout:
                    assert pending.result(timeout=10) is None
            finally:
                reader_ready.set()
        first_connections = list(connections)
        if hold_until_timeout:
            service.recover(runtime.scheduler)  # Explicit recovery of the same receipt/request, not a retry policy.
        after = _sqlite_table_facts(repository, tables)
        assert {row["id"] for row in after["job_runs"]} == {row["id"] for row in before["job_runs"]}
        assert {row["id"] for row in after["device_work_requests"]} == {row["id"] for row in before["device_work_requests"]}
        finished = next(row for row in after["device_work_requests"] if row["id"] == original["id"])
        assert (finished["state"], finished["checkpoint_ref"], finished["attempt_number"], finished["last_grant_generation"]) == (
            "done", event_id, original["attempt_number"], original["last_grant_generation"])
        assert repository.get_job_run("local", job_id).status == "succeeded"
        assert checks.get_check("local", "task", check_id)["result_status"] == "unavailable"
        assert checks.get("local", "task")["check_id"] == check_id
        assert [row["id"] for row in after["task_events"] if row["event_type"] == "quality_check_terminal"] == [event_id]
        assert after["device_request_events"] == before["device_request_events"]
        assert after["device_permit"] == before["device_permit"]
        service.recover(runtime.scheduler)
        assert _sqlite_table_facts(repository, tables) == after
        assert all(item["database"] == str(repository.database_path) for item in first_connections)
        print("SQLITE_LOCK_EVIDENCE " + json.dumps({"owner": "quality.recover", "timeout": hold_until_timeout,
            "reader": reader_facts, "writer_connections": first_connections, "elapsed_seconds": elapsed,
            "request_id": original["id"], "job_id": job_id, "check_id": check_id,
            "one_terminal_checkpoint": True, "timeout_rollback_verified": hold_until_timeout}, sort_keys=True))
    finally:
        assert runtime.stop(1)


def test_recovery_terminal_request_update_rolls_back_and_retries_same_receipt(tmp_path, monkeypatch):
    repository = ProductionRepository(tmp_path / "studio.sqlite", tmp_path / "library")
    plan = CreationPlan(id="plan", workspace_id="local", goal="测试", name="质检", target_count=1, language="zh-CN",
        aspect_ratio="16:9", duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0, material_rule_summary="", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint="isolated")
    task = CreationTask(id="task", workspace_id="local", plan_id="plan", name="质检", scenario="knowledge_explanation", stage="review")
    repository.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id="member")
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
        sealed = stage_metadata_prefix(runtime, service, actor, check_id)
        result = metadata_result(sealed)  # Explicit already-returned metadata port receipt.
        with pytest.raises(ValueError, match="完整文件 SHA"):
            service.validate_result(sealed, {key: value for key, value in result.items() if key != "file_sha256"})
        with pytest.raises(ValueError, match="当前阶段"):
            service.validate_result(sealed, {**result, "phase": "clipping"})
        checks.store_worker_result("local", "task", check_id, result)
        service.budget = None

        def facts():
            with repository._connect() as conn:
                return {table: sorted([dict(row) for row in conn.execute(f"SELECT * FROM {table}")], key=lambda row: json.dumps(row, sort_keys=True))
                    for table in ("creation_task_quality_checks", "creation_task_quality_history", "job_runs", "task_events",
                                  "device_permit", "device_work_requests", "device_request_events")}

        before = facts()
        generation = runtime.scheduler.reconcile_released()
        source = sealed["source"]
        expected = VerifiedWork("local", "quality_check", job_id, repository.get_job_run("local", job_id).input_digest,
            f"quality:{check_id}:finalize", actor.member_id, "normal_make", sealed["resources"],
            (SourceRef("local", "artifact", source["artifact_id"], source["render_ref"]),))
        for candidate, candidate_generation in [(replace(expected, unit_key=f"quality:{check_id}:missing"), generation),
                                                (expected, generation - 1)]:
            with repository._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                with pytest.raises(RecoveryBlocked):
                    runtime.scheduler.complete_recovered_quality(conn, candidate, generation=candidate_generation,
                        checkpoint_ref=event_id, status="succeeded")
        assert facts() == before  # Wrong phase/generation cannot settle another unit.
        original_attempt = next(row["attempt_number"] for row in before["device_work_requests"] if row["owner_id"] == job_id and row["unit_key"].endswith(":finalize"))
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
        assert repository.execution_records.get_task_event("local", event_id).payload == {"check_id": check_id, "status": "succeeded"}
        with repository._connect() as conn:
            request = dict(conn.execute("SELECT * FROM device_work_requests WHERE owner_id=? AND unit_key LIKE '%:finalize'", (job_id,)).fetchone())
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
