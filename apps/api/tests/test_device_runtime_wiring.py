"""Scheduled material routes must never fall through to unscheduled heavy work."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Request

from app import main as main_module


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/production/materials"})


def _material_routes(monkeypatch: pytest.MonkeyPatch) -> Mock:
    repository = Mock()
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


def test_scheduled_scan_uses_discovery_and_manual_heavy_routes_stay_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _material_routes(monkeypatch)
    scheduler = object()
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
