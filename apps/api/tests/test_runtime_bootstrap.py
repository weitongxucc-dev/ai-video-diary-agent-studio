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


def test_device_stop_false_retains_permit_and_does_not_replace_running_unit(isolated_host, tmp_path, monkeypatch):
    from test_device_runtime import _runtime, _intent
    main._reload_runtime_dependencies()
    worker, ingestion = main.execution_worker, main.material_ingestion
    entered, release = threading.Event(), threading.Event()
    class BlockingUnit:
        def execute(self, grant, scheduler):
            entered.set()
            assert release.wait(5)
            scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                checkpoint_write=lambda conn, current: current.owner_id)
    device_root = tmp_path / "device"
    device_root.mkdir()
    runtime = _runtime(device_root, database=main.production_repo.database_path, executor=BlockingUnit())
    runtime.scheduler.submit(_intent("first"), "member")
    runtime.start()
    assert entered.wait(2)
    stop = runtime.stop
    monkeypatch.setattr(runtime, "stop", lambda timeout_seconds: stop(0.01))
    monkeypatch.setattr(main, "device_runtime", runtime)
    old_settings, old_catalog = main.settings, main.model_installer.catalog
    old_tmp = os.environ["TMPDIR"], tempfile.tempdir
    try:
        response = client().post("/api/settings/runtime", json={"model_root_path": str(tmp_path / "next-models")})
        assert response.status_code == 500, response.text
        assert main.device_runtime is runtime and runtime.running
        assert main.settings is old_settings and main.model_installer.catalog is old_catalog
        assert (os.environ["TMPDIR"], tempfile.tempdir) == old_tmp
        assert "next-models" in isolated_host[3].read_text()  # persistence precedes an unsuccessful stop
        with sqlite3.connect(runtime.scheduler.database_path) as conn:
            assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "held"
        assert_legacy_worker_usable(worker, ingestion)
        (tmp_path / "stop-state.json").write_text(json.dumps({
            "owner": "device", "env_candidate_persisted": True,
            "workspace_or_secret_submitted": False, "installer_binding_changed": False,
            "runtime_globals_changed": False, "process_tmp_changed": False,
            "device_thread_alive": runtime.running, "device_closed": runtime._closed,
            "permit": "held", "legacy_worker_submit": "ready", "ingestion_thread_alive": True,
            "replacement_coordinator": False,
        }))
    finally:
        release.set()
        assert stop(2)
        main.device_runtime = None


@pytest.mark.parametrize("owner", ["automation", "continuation"])
def test_unfinished_background_stop_keeps_bindings_but_records_persisted_config_limit(isolated_host, tmp_path, monkeypatch, owner):
    main._reload_runtime_dependencies()
    worker, ingestion = main.execution_worker, main.material_ingestion
    entered, release = threading.Event(), threading.Event()
    def blocked():
        if threading.current_thread() is not threading.main_thread():
            entered.set()
            assert release.wait(5)
        return 0
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
    join = driver.thread.join
    monkeypatch.setattr(driver.thread, "join", lambda timeout=None: join(0.01))
    old_settings, old_catalog, old_tmp = main.settings, main.model_installer.catalog, os.environ["TMPDIR"]
    try:
        response = client().post("/api/settings/runtime", json={
            "model_root_path": str(tmp_path / "next-models"), "llm_provider": "deepseek",
            "llm_model": "newly-persisted", "llm_api_key": "synthetic-stop-test-secret",
        })
        assert response.status_code == 500, response.text
        assert main.settings is old_settings and main.model_installer.catalog is old_catalog
        assert os.environ["TMPDIR"] == old_tmp
        assert driver.thread.is_alive()
        assert "next-models" in isolated_host[3].read_text()
        assert_legacy_worker_usable(worker, ingestion)
        from app.workspace_settings import load_workspace_config, read_workspace_secret
        assert load_workspace_config(LEGACY_WORKSPACE_ID)["llm_model"] == "newly-persisted"
        assert read_workspace_secret(LEGACY_WORKSPACE_ID, "llm_api_key") == "synthetic-stop-test-secret"
        (tmp_path / "stop-state.json").write_text(json.dumps({
            "owner": owner, "env_candidate_persisted": True, "workspace_config_persisted": True,
            "synthetic_secret_store_persisted": True, "secret_value_recorded": False,
            "installer_binding_changed": False, "runtime_globals_changed": False,
            "process_tmp_changed": False, "driver_thread_alive": True,
            "driver_stop_requested": driver.stop_event.is_set(),
            "legacy_worker_submit": "ready", "ingestion_thread_alive": True,
            "whole_configuration_runtime_atomic": False,
        }))
    finally:
        release.set()
        join(2)
        assert not driver.thread.is_alive()
        if owner == "continuation":
            main.assistant_continuation_driver = None
