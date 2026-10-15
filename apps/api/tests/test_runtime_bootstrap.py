"""Actual runtime bindings and failed preparation with synthetic device paths."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.repository import LEGACY_WORKSPACE_ID
from app.scheduled_tasks import ScheduledTaskCreateRequest
from test_host_model_installation import isolated_host as isolated_host


def client():
    return TestClient(main.app, client=("127.0.0.1", 50000), raise_server_exceptions=False)


def assert_legacy_worker_usable(worker, ingestion):
    assert worker.submit(lambda: "ready").result(timeout=2) == "ready"
    assert ingestion._thread is not None and ingestion._thread.is_alive()


def configuration_state(env_path):
    from app.workspace_settings import load_workspace_config, read_workspace_secret
    return (env_path.read_bytes(), dict(os.environ), tempfile.tempdir,
        load_workspace_config(LEGACY_WORKSPACE_ID), read_workspace_secret(LEGACY_WORKSPACE_ID, "llm_api_key"))


def next_configuration(tmp_path):
    return {"model_root_path": str(tmp_path / "next-models"), "llm_provider": "deepseek",
        "llm_model": "must-not-persist", "llm_api_key": "synthetic-unwritten-secret"}


def healthy_idle_predecessor(tmp_path, monkeypatch, later_owner):
    completed = threading.Event()
    if later_owner == "automation":
        from test_device_runtime import _runtime, CompletingExecutor
        class Observer(CompletingExecutor):
            def execute(self, grant, scheduler):
                super().execute(grant, scheduler)
                completed.set()
        root = tmp_path / "predecessor-device"
        root.mkdir()
        predecessor = _runtime(root, database=main.production_repo.database_path, executor=Observer())
        predecessor.start()
        monkeypatch.setattr(main, "device_runtime", predecessor)
    else:
        predecessor = main._automation_runtime()
        predecessor.service.tick = completed.set
        predecessor.start()
        assert completed.wait(2)
        completed.clear()
    return predecessor, completed


def test_database_switch_moves_five_actual_service_writes_together(isolated_host, tmp_path):
    old_db = Path(main.settings.database_path)
    main.conversation_repo.create("probe", "member", "home", "home", "OLD")
    new_db = tmp_path / "new" / "studio.sqlite3"
    response = client().post("/api/settings/runtime", json={"database_path": str(new_db)})
    assert response.status_code == 200, response.text
    assert Path(main.settings.database_path) == Path(main.production_repo.database_path) == new_db
    assert main.conversation_repo.current_or_create("probe", "member", "home", "home")["title"] != "OLD"
    main.conversation_repo.create("probe", "member", "home", "home", "AFTER")
    main.quality_check_repo.begin("probe", "after-task", "after-version", None, None)
    main.local_members_service.add_member("AFTER")
    main.scheduled_task_service.create_task("probe", ScheduledTaskCreateRequest(
        task_type="creation_render", trigger_mode="idle", target_ref="AFTER",
    ))

    class SyntheticObservation:
        def open(self, *args):
            pass

        def invoke(self, *args):
            return SimpleNamespace(logged_in=True, account_ref="AFTER", detail="synthetic browser observation")

    main.platform_auth_service.sessions = SyntheticObservation()
    main.platform_auth_service.verify_current("probe", "member", "douyin")
    from app.platform_auth.storage import _open
    with _open(old_db):
        pass
    for table, field, marker in (
        ("assistant_conversations", "title", "AFTER"),
        ("creation_task_quality_checks", "task_id", "after-task"),
        ("local_members", "name", "AFTER"),
        ("scheduled_tasks", "target_ref", "AFTER"),
        ("platform_browser_accounts", "account_ref", "AFTER"),
    ):
        counts = []
        for database in (old_db, new_db):
            with sqlite3.connect(database) as conn:
                counts.append(conn.execute(f"SELECT count(*) FROM {table} WHERE {field}=?", (marker,)).fetchone()[0])
        assert counts == [0, 1], (table, counts)


def test_invalid_tmp_refuses_before_env_workspace_installer_and_driver_changes(isolated_host, tmp_path, monkeypatch):
    installer, _, _, env_path, _ = isolated_host
    main._reload_runtime_dependencies()
    worker, ingestion, settings, catalog = main.execution_worker, main.material_ingestion, main.settings, main.model_catalog
    before = env_path.read_bytes(), dict(os.environ), tempfile.tempdir
    invalid = tmp_path / "ordinary-file"
    invalid.write_text("not a directory")
    def forbidden(*args):
        pytest.fail("invalid candidate must not persist workspace values or rebind installer")
    monkeypatch.setattr(main, "apply_workspace_runtime_settings_update", forbidden)
    monkeypatch.setattr(installer, "rebind_catalog", forbidden)
    response = client().post("/api/settings/runtime", json={
        "temporary_root_path": str(invalid), "model_root_path": str(tmp_path / "next-models"), "llm_model": "not-saved",
    })
    assert response.status_code == 500, response.text
    assert "FileExistsError" in response.text
    assert (env_path.read_bytes(), dict(os.environ), tempfile.tempdir) == before
    assert main.settings is settings and main.model_catalog is catalog
    assert main.execution_worker is worker and main.material_ingestion is ingestion
    assert_legacy_worker_usable(worker, ingestion)


def test_same_database_reload_retains_pending_invite_and_identity_owner(isolated_host):
    members = main.local_members_service
    invite = members.create_invite_code()
    main._reload_runtime_dependencies()
    assert main.local_members_service is members
    member, token = members.join(invite, "after reload")
    assert member.name == "after reload" and token


def test_reloaded_platform_route_uses_new_auth_owner_without_duplicate_urls(isolated_host, tmp_path):
    response = client().post("/api/settings/runtime", json={"database_path": str(tmp_path / "next.sqlite3")})
    assert response.status_code == 200, response.text
    member, token = main.local_members_service.join(main.local_members_service.create_invite_code(), "new reader")
    class SyntheticObservation:
        def open(self, *args):
            pass

        def invoke(self, *args):
            return SimpleNamespace(logged_in=True, account_ref="NEW account", detail="synthetic browser observation")

    main.platform_auth_service.sessions = SyntheticObservation()
    main.platform_auth_service.verify_current(LEGACY_WORKSPACE_ID, member.member_id, "douyin")
    accounts = client().get("/api/publishing/accounts", headers={"X-Local-Member-Token": token})
    assert accounts.status_code == 200, accounts.text
    assert [account["account_ref"] for account in accounts.json()["items"]] == ["NEW account"]
    keys = [(route.path, tuple(sorted(route.methods))) for route in main.app.routes
        if getattr(route, "path", "").startswith("/api/publishing/")]
    assert len(keys) == len(set(keys))


def test_stream_in_progress_refuses_reload_before_configuration_write(isolated_host, tmp_path):
    entered, release = threading.Event(), threading.Event()
    def execute(publish):
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(model_dump=lambda **kwargs: {}, turn_status="completed")
    stream = main._assistant_stream_response(execute, workspace_id=LEGACY_WORKSPACE_ID)
    async def consume():
        async for _ in stream.body_iterator:
            pass
    reader = threading.Thread(target=lambda: asyncio.run(consume()))
    before = isolated_host[3].read_bytes()
    reader.start()
    try:
        assert entered.wait(2)
        response = client().post("/api/settings/runtime", json={"model_root_path": str(tmp_path / "next-models")})
        assert response.status_code == 503, response.text
        assert isolated_host[3].read_bytes() == before
    finally:
        release.set()
        reader.join(5)
        assert not reader.is_alive()
    assert not main._assistant_stream_workers


def test_busy_device_pause_refuses_before_config_and_original_driver_continues(isolated_host, tmp_path, monkeypatch):
    from test_device_runtime import _runtime, _intent
    main._reload_runtime_dependencies()
    entered, release, continued = threading.Event(), threading.Event(), threading.Event()
    calls = []
    class Unit:
        def execute(self, grant, scheduler):
            calls.append(grant.owner_id)
            if grant.owner_id == "first":
                entered.set()
                assert release.wait(5)
            scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                checkpoint_write=lambda conn, current: current.owner_id)
            if grant.owner_id == "second":
                continued.set()
    device_root = tmp_path / "device"
    device_root.mkdir()
    runtime = _runtime(device_root, database=main.production_repo.database_path, executor=Unit())
    runtime.scheduler.submit(_intent("first"), "member")
    runtime.scheduler.submit(_intent("second"), "member")
    runtime.start()
    assert entered.wait(2)
    thread = runtime._thread
    pause = runtime.pause
    monkeypatch.setattr(runtime, "pause", lambda timeout_seconds: pause(0.01))
    monkeypatch.setattr(main, "device_runtime", runtime)
    before = configuration_state(isolated_host[3])
    try:
        response = client().post("/api/settings/runtime", json=next_configuration(tmp_path))
        assert response.status_code == 503, response.text
        assert configuration_state(isolated_host[3]) == before
        assert main.device_runtime is runtime and runtime._thread is thread
        assert not runtime._closed and not runtime._stop.is_set() and not runtime._paused.is_set()
        with sqlite3.connect(runtime.scheduler.database_path) as conn:
            assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "held"
        release.set()
        assert continued.wait(2)
        assert calls == ["first", "second"] and runtime.last_error is None
        assert runtime._thread is thread
        (tmp_path / "pause-state.json").write_text(json.dumps({"owner": "device", "configuration_written": False,
            "closed": False, "stop_requested": False, "same_driver": True, "held_unit_finished": True,
            "next_queued_unit_completed": True, "replacement_coordinator": False}))
    finally:
        release.set()
        assert runtime.stop(2)
        main.device_runtime = None



@pytest.mark.parametrize("owner", ["automation", "continuation"])
def test_busy_background_pause_refuses_and_restores_previous_owner(isolated_host, tmp_path, monkeypatch, owner):
    main._reload_runtime_dependencies()
    entered, release, continued = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def blocked():
        if threading.current_thread() is not threading.main_thread():
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                assert release.wait(5)
            else:
                continued.set()
        return 0
    predecessor, predecessor_done = healthy_idle_predecessor(tmp_path, monkeypatch, owner)
    if owner == "automation":
        driver = main._automation_runtime()
        monkeypatch.setattr(driver.service, "tick", blocked)
    else:
        monkeypatch.setattr(main, "_reconcile_assistant_business_results", blocked)
        driver = main.AssistantContinuationDriver()
        monkeypatch.setattr(main, "assistant_continuation_driver", driver)
    driver.start()
    if owner == "continuation":
        driver.wake.set()
    assert entered.wait(2)
    thread = driver.thread
    pause = driver.pause
    monkeypatch.setattr(driver, "pause", lambda timeout_seconds: pause(0.01))
    before = configuration_state(isolated_host[3])
    try:
        response = client().post("/api/settings/runtime", json=next_configuration(tmp_path))
        assert response.status_code == 503, response.text
        assert configuration_state(isolated_host[3]) == before
        assert driver.thread is thread and not driver.stop_event.is_set() and not driver._paused.is_set()
        assert not predecessor._paused.is_set()
        if owner == "automation":
            from test_device_runtime import _intent
            predecessor.scheduler.submit(_intent("after-rollback"), "member")
            predecessor.wake()
        else:
            predecessor._wake.set()
        assert predecessor_done.wait(2)
        release.set()
        (driver._wake if owner == "automation" else driver.wake).set()
        assert continued.wait(2) and driver.thread is thread
        (tmp_path / "pause-state.json").write_text(json.dumps({"owner": owner, "configuration_written": False,
            "stop_requested": False, "same_driver": True, "original_loop_tick_after_refusal": True,
            "previous_paused_owner_resumed_and_ran": True}))
    finally:
        release.set()
        driver.stop()
        if owner == "automation":
            assert predecessor.stop(2)
            main.device_runtime = None
        else:
            main.assistant_continuation_driver = None


def test_device_persistence_error_resumes_all_original_loops(isolated_host, tmp_path, monkeypatch):
    main._reload_runtime_dependencies()
    device, completed = healthy_idle_predecessor(tmp_path, monkeypatch, "automation")
    automatic = main._automation_runtime()
    auto_tick, conversation_tick = threading.Event(), threading.Event()
    automatic.service.tick = auto_tick.set
    automatic.start()
    assert auto_tick.wait(2)
    auto_tick.clear()
    def reconcile():
        if threading.current_thread() is not threading.main_thread():
            conversation_tick.set()
        return 0
    monkeypatch.setattr(main, "_reconcile_assistant_business_results", reconcile)
    continuation = main.AssistantContinuationDriver()
    monkeypatch.setattr(main, "assistant_continuation_driver", continuation)
    continuation.start()
    threads = device._thread, automatic.thread, continuation.thread
    bad_env = tmp_path / "env-is-directory"
    bad_env.mkdir()
    monkeypatch.setattr(main, "runtime_env_path", bad_env)
    before = configuration_state(isolated_host[3])
    try:
        response = client().post("/api/settings/runtime", json={"model_root_path": str(tmp_path / "next-models")})
        assert response.status_code == 500, response.text
        assert "IsADirectoryError" in response.text
        assert configuration_state(isolated_host[3]) == before
        for owner in (device, automatic, continuation):
            assert not owner._paused.is_set()
        from test_device_runtime import _intent
        device.scheduler.submit(_intent("after-io-failure"), "member")
        device.wake()
        assert completed.wait(2) and auto_tick.wait(2) and conversation_tick.wait(2)
        assert (device._thread, automatic.thread, continuation.thread) == threads
    finally:
        continuation.stop()
        main.assistant_continuation_driver = None
        assert device.stop(2)
        main.device_runtime = None


def test_successful_switch_retires_old_automation_and_resumes_same_continuation(isolated_host, tmp_path, monkeypatch):
    main._reload_runtime_dependencies()
    automatic = main._automation_runtime()
    automatic.service.tick = lambda: None
    automatic.start()
    updated = threading.Event()
    next_root = tmp_path / "next-models"
    def reconcile():
        if Path(main.settings.model_root_path) == next_root:
            updated.set()
        return 0
    monkeypatch.setattr(main, "_reconcile_assistant_business_results", reconcile)
    continuation = main.AssistantContinuationDriver()
    monkeypatch.setattr(main, "assistant_continuation_driver", continuation)
    continuation.start()
    thread = continuation.thread
    response = client().post("/api/settings/runtime", json={"model_root_path": str(next_root)})
    assert response.status_code == 200, response.text
    assert "next-models" in isolated_host[3].read_text()
    assert not automatic.thread.is_alive()
    assert main.automation_runtime is not automatic and main.automation_runtime.thread.is_alive()
    assert main.assistant_continuation_driver is continuation and continuation.thread is thread
    assert updated.wait(2) and not continuation._paused.is_set()
    continuation.stop()
    main.assistant_continuation_driver = None
    with pytest.raises(RuntimeError, match="已终止"):
        continuation.resume()


def test_terminal_device_during_quiescence_exposes_unrecoverable_owner(isolated_host, tmp_path, monkeypatch):
    main._reload_runtime_dependencies()
    device, _ = healthy_idle_predecessor(tmp_path, monkeypatch, "automation")
    before = configuration_state(isolated_host[3])
    try:
        with pytest.raises(RuntimeError, match="旧运行恢复失败.*device"):
            with main._quiesced_runtime_drivers():
                assert device._paused.is_set()
                assert device.stop(2)  # independently terminated owner cannot be resumed
        assert configuration_state(isolated_host[3]) == before
        assert main.device_runtime is device and device._closed
    finally:
        device.stop(2)
        main.device_runtime = None


def test_quiescence_preserves_primary_failure_and_reports_secondary_recovery(monkeypatch):
    from fastapi import HTTPException

    calls = []

    class RecoveryOwner:
        def __init__(self, name, failure=None):
            self.name, self.failure = name, failure

        def pause(self, timeout):
            calls.append(('pause', self.name, timeout))
            return True

        def resume(self):
            calls.append(('resume', self.name))
            if self.failure is not None:
                raise self.failure

    device_failure = RuntimeError('isolated device recovery refused')
    monkeypatch.setattr(main, 'device_runtime', RecoveryOwner('device', device_failure))
    monkeypatch.setattr(main, 'automation_runtime', RecoveryOwner('automation'))

    def stopped_continuation_fixture():
        driver = main.AssistantContinuationDriver()
        # A joinable/stopped thread port keeps this an exception-priority unit test.
        # Original continuation pause/stop/resume bodies execute; no thread is started.
        driver.thread = SimpleNamespace(join=lambda timeout: None, is_alive=lambda: False)
        original_pause, original_resume = driver.pause, driver.resume
        resume_failures = []

        def observe_pause(timeout):
            calls.append(('pause', 'continuation', timeout))
            return original_pause(timeout)

        def observe_resume():
            calls.append(('resume', 'continuation'))
            try:
                return original_resume()
            except RuntimeError as exc:
                resume_failures.append(exc)
                raise

        monkeypatch.setattr(driver, 'pause', observe_pause)
        monkeypatch.setattr(driver, 'resume', observe_resume)
        monkeypatch.setattr(main, 'assistant_continuation_driver', driver)
        return driver, original_resume, resume_failures

    continuation, activation_resume, secondary_failures = stopped_continuation_fixture()
    primary = HTTPException(503, '配置已保存，运行重载失败；请检查运行状态。')
    activation_failures = []
    with pytest.raises(HTTPException) as caught:
        with main._quiesced_runtime_drivers() as drivers:
            assert not drivers.committed
            continuation.stop()
            try:
                activation_resume()
            except RuntimeError as exc:
                activation_failures.append(exc)
                raise primary from exc
    assert caught.value is primary and caught.value.status_code == 503
    assert primary.__cause__ is activation_failures[0]
    assert '已终止' in str(primary.__cause__)
    assert primary.__cause__ is not secondary_failures[0]
    assert calls == [('pause', 'device', 30.0), ('pause', 'automation', 10.0), ('pause', 'continuation', 5.0),
        ('resume', 'continuation'), ('resume', 'automation'), ('resume', 'device')]
    assert len(primary.__notes__) == 2
    assert any('continuation' in note and str(secondary_failures[0]) in note for note in primary.__notes__)
    assert any('device' in note and str(device_failure) in note for note in primary.__notes__)
    assert not drivers.committed

    calls.clear()
    continuation, _, secondary_failures = stopped_continuation_fixture()
    # An unrelated already-handled exception is not a pending body failure.
    try:
        raise LookupError('outer handled failure')
    except LookupError:
        with pytest.raises(RuntimeError, match='旧运行恢复失败.*continuation.*device') as recovery:
            with main._quiesced_runtime_drivers() as no_primary_drivers:
                continuation.stop()
    assert recovery.value.__cause__ is secondary_failures[0]
    assert calls == [('pause', 'device', 30.0), ('pause', 'automation', 10.0), ('pause', 'continuation', 5.0),
        ('resume', 'continuation'), ('resume', 'automation'), ('resume', 'device')]
    assert not no_primary_drivers.committed
    assert continuation.thread.is_alive() is False

    destination = os.environ.get('LATE_ACTIVATION_PRIMARY_C1_EVIDENCE_DIR')
    if destination:
        (Path(destination) / 'NORMAL_SUMMARY.json').write_text(json.dumps({
            'primary_type': type(primary).__name__, 'primary_status': primary.status_code,
            'same_primary_object': caught.value is primary,
            'same_original_cause': primary.__cause__ is activation_failures[0],
            'primary_cause': str(primary.__cause__), 'secondary_notes': primary.__notes__,
            'without_primary_error': str(recovery.value),
            'without_primary_cause_is_original_first_recovery_error': recovery.value.__cause__ is secondary_failures[0],
            'resume_order_both_cases': ['continuation', 'automation', 'device'],
            'actual_continuation_pause_stop_resume': True,
            'fixture_boundary': 'Finite device/automation recovery ports and joinable stopped-thread port; no real threads, lifecycle replay or configuration persistence claimed.',
            'committed_forced_or_resume_skipped': False, 'native_models_resources_real_threads': 0,
        }, ensure_ascii=False, indent=2) + '\n')
