"""Scheduled material routes must never fall through to unscheduled heavy work."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Request

from app import main as main_module
from app.device_runtime import RuntimeUnavailable
from app.production import ProductionRepository


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/production/materials"})


def _material_routes(monkeypatch: pytest.MonkeyPatch, database_path=None) -> Mock:
    repository = Mock()
    repository.database_path = database_path
    repository.list_assets_page.return_value = ([], 0)
    monkeypatch.setattr(main_module, "production_repo", repository)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda request: "workspace-one")
    monkeypatch.setattr(
        main_module, "_material_binding_status",
        lambda workspace_id, repo: SimpleNamespace(
            bound_to_current_workspace=True, directory_state="ready", directory_message="",
        ),
    )
    monkeypatch.setattr(main_module, "material_execution_mode", "scheduled")
    return repository


def test_scheduled_material_routes_reject_missing_driver_before_heavy_work(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _material_routes(monkeypatch)
    monkeypatch.setattr(main_module, "device_runtime", None)
    monkeypatch.setattr(main_module, "material_ingestion", None)

    routes = (
        lambda: main_module.scan_material_intake(_request()),
        lambda: main_module.retry_material_intake("asset-one", _request()),
        lambda: main_module.scan_production_materials(_request()),
        lambda: main_module.analyze_production_material("asset-one", _request()),
    )
    for route in routes:
        with pytest.raises(HTTPException) as error:
            route()
        assert error.value.status_code == 503

    repository.scan_library.assert_not_called()
    repository.analyze_asset.assert_not_called()
    repository.retry_material_ingestion.assert_not_called()


def test_scheduled_scan_uses_discovery_and_manual_heavy_routes_stay_closed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    repository = _material_routes(monkeypatch, tmp_path / "task.sqlite")
    scheduler = SimpleNamespace(database_path=tmp_path / "task.sqlite")
    service = SimpleNamespace(
        repository=repository,
        execution_mode="scheduled",
        scheduler=scheduler,
        scan_once=Mock(return_value=[]),
    )
    runtime = SimpleNamespace(scheduler=scheduler, running=True, last_error=None)
    monkeypatch.setattr(main_module, "device_runtime", runtime)
    monkeypatch.setattr(main_module, "material_ingestion", service)

    result = main_module.scan_production_materials(_request())
    assert result.total_count == 0
    service.scan_once.assert_called_once_with()
    repository.scan_library.assert_not_called()

    for route in (
        lambda: main_module.retry_material_intake("asset-one", _request()),
        lambda: main_module.analyze_production_material("asset-one", _request()),
    ):
        with pytest.raises(HTTPException) as error:
            route()
        assert error.value.status_code == 503
    repository.retry_material_ingestion.assert_not_called()
    repository.analyze_asset.assert_not_called()


def test_scheduled_scan_rejects_scheduler_from_another_database(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    repository = _material_routes(monkeypatch, tmp_path / "task.sqlite")
    scheduler = SimpleNamespace(database_path=tmp_path / "other.sqlite")
    service = SimpleNamespace(
        repository=repository,
        execution_mode="scheduled",
        scheduler=scheduler,
        scan_once=Mock(return_value=[]),
    )
    monkeypatch.setattr(main_module, "device_runtime", SimpleNamespace(scheduler=scheduler, running=True, last_error=None))
    monkeypatch.setattr(main_module, "material_ingestion", service)

    with pytest.raises(HTTPException) as error:
        main_module.scan_production_materials(_request())
    assert error.value.status_code == 503
    service.scan_once.assert_not_called()
    repository.scan_library.assert_not_called()


def test_scheduled_mode_cannot_start_legacy_worker_or_stop_it_during_unwired_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "material_execution_mode", "scheduled")
    new_legacy_service = Mock()
    stop_execution = Mock()
    stop_ingestion = Mock()
    monkeypatch.setattr(main_module, "_new_material_ingestion", new_legacy_service)
    monkeypatch.setattr(main_module, "_stop_execution_worker", stop_execution)
    monkeypatch.setattr(main_module, "_stop_material_ingestion", stop_ingestion)

    with pytest.raises(RuntimeError, match="统一启动尚未接通"):
        main_module._start_material_ingestion()
    with pytest.raises(RuntimeError, match="安全重载尚未接通"):
        main_module._reload_runtime_dependencies()
    new_legacy_service.assert_not_called()
    stop_execution.assert_not_called()
    stop_ingestion.assert_not_called()


def test_hash_runtime_candidate_binds_one_repository_without_starting(tmp_path) -> None:
    repository = ProductionRepository(tmp_path / "production.sqlite", tmp_path / "library")
    runtime, service = main_module._build_scheduled_material_components(
        repository=repository,
        device_lock_path=tmp_path / "device.lock",
        device_access=Mock(),
        resources=Mock(),
        processes=Mock(),
    )

    assert runtime.running is False
    assert service.repository is repository
    assert service.execution_mode == "scheduled"
    assert service.scheduler is runtime.scheduler
    assert runtime.scheduler.database_path == repository.database_path.resolve()
    assert set(runtime.registrations) == {"material_asset"}
    assert runtime.registrations["material_asset"].executor._service is service


@pytest.mark.parametrize("missing", ["device_access", "resources", "processes"])
def test_hash_runtime_candidate_requires_explicit_host_adapters(tmp_path, missing) -> None:
    repository = ProductionRepository(tmp_path / "production.sqlite", tmp_path / "library")
    adapters = {"device_access": Mock(), "resources": Mock(), "processes": Mock()}
    adapters[missing] = None
    with pytest.raises(RuntimeUnavailable, match="探针|权限"):
        main_module._build_scheduled_material_components(
            repository=repository,
            device_lock_path=tmp_path / "device.lock",
            **adapters,
        )
