"""Scheduled material routes must never fall through to unscheduled heavy work."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Request

from app import main as main_module
from app.member_permits import HostBudgetUncalibrated
from app.production import ProductionRepository

#: 一台机器标定完成后会拿到的真实数值。缺任何一项都应当被拒绝，不是给个默认。
_CALIBRATED_ENV = {
    "DEVICE_HOST_MAX_CPU_PERCENT": "60",
    "DEVICE_HOST_MIN_AVAILABLE_MEMORY_BYTES": "1073741824",
    "DEVICE_HOST_MAX_SWAP_USED_BYTES": "1073741824",
    "DEVICE_HOST_MIN_FREE_DISK_BYTES": "21474836480",
    "DEVICE_HOST_MAX_DISK_IO_BYTES_PER_SECOND": "20971520",
    "DEVICE_HOST_MAX_UNIT_INPUT_BYTES": "268435456",
    "DEVICE_CPU_RENDER_MAX_DURATION_SECONDS": "300",
    "DEVICE_CPU_RENDER_MAX_PIXEL_SECONDS": "200000000",
    "DEVICE_CPU_RENDER_MAX_WIDTH_PX": "1920",
    "DEVICE_CPU_RENDER_MAX_HEIGHT_PX": "1080",
    "DEVICE_CPU_RENDER_MAX_INPUT_BYTES": "268435456",
    "DEVICE_CPU_RENDER_MAX_CLIP_COUNT": "60",
    "DEVICE_CPU_RENDER_MAX_OVERLAY_COUNT": "40",
    "DEVICE_CPU_RENDER_MAX_OUTPUT_BYTES": "8000000",
    "DEVICE_CPU_RENDER_MAX_THREADS": "2",
    "DEVICE_CPU_RENDER_MAX_TIMEOUT_SECONDS": "900",
    "DEVICE_CPU_RENDER_ADDITIONAL_MEMORY_BYTES": "1073741824",
    "DEVICE_CPU_RENDER_ADDITIONAL_DISK_BYTES": "10737418240",
    "DEVICE_NARRATION_MAX_TEXT_CHARS": "1000",
    "DEVICE_NARRATION_MAX_SECTION_COUNT": "10",
    "DEVICE_NARRATION_MAX_REFERENCE_BYTES": "1000000",
    "DEVICE_NARRATION_MAX_DURATION_SECONDS": "300",
    "DEVICE_NARRATION_MAX_OUTPUT_BYTES": "8000000",
    "DEVICE_NARRATION_MAX_THREADS": "2",
    "DEVICE_NARRATION_MAX_TIMEOUT_SECONDS": "300",
    "DEVICE_NARRATION_MAX_PROCESS_RSS_BYTES": "1073741824",
    "DEVICE_NARRATION_MAX_PROCESS_THREADS": "16",
    "DEVICE_NARRATION_RUNTIME_SAMPLE_SECONDS": "0.1",
    "DEVICE_NARRATION_ADDITIONAL_MEMORY_BYTES": "1073741824",
    "DEVICE_NARRATION_ADDITIONAL_DISK_BYTES": "10000000",
    "DEVICE_MONTAGE_MAX_SECTION_COUNT": "10",
    "DEVICE_MONTAGE_MAX_ASSET_COUNT": "20",
    "DEVICE_MONTAGE_MAX_INPUT_BYTES": "1000000",
    "DEVICE_MONTAGE_MAX_OUTPUT_BYTES": "1000000",
    "DEVICE_MONTAGE_MAX_TIMEOUT_SECONDS": "10",
    "DEVICE_MONTAGE_ADDITIONAL_MEMORY_BYTES": "1073741824",
}


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/production/materials", "headers": []})


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

    with pytest.raises(RuntimeError, match="尚未接入整机许可运行时"):
        main_module._start_material_ingestion()
    with pytest.raises(RuntimeError, match="安全重载尚未接通"):
        main_module._reload_runtime_dependencies()
    new_legacy_service.assert_not_called()
    stop_execution.assert_not_called()
    stop_ingestion.assert_not_called()


def test_one_runtime_carries_creation_job_and_scheduled_material_without_starting(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一把锁、一张队列、一个驱动线程；交互许可不因定时通道关闭而缺席。"""
    repository = ProductionRepository(tmp_path / "production.sqlite", tmp_path / "library")
    monkeypatch.setattr(main_module, "production_repo", repository)
    monkeypatch.setattr(main_module, "material_execution_mode", "scheduled")
    for name, value in _CALIBRATED_ENV.items():
        monkeypatch.setenv(name, value)

    runtime, service = main_module._build_device_components(repository=repository)

    # 只装好、不启动：驱动线程由生命周期统一启停，装配阶段不得自己起。
    assert runtime.running is False
    assert set(runtime.registrations) == {"creation_job", "platform_publish", "material_asset"}
    # 素材服务拿的是同一个 runtime 的同一张队列，不是第二条。
    assert service is not None
    assert service.repository is repository
    assert service.execution_mode == "scheduled"
    assert service.scheduler is runtime.scheduler
    assert runtime.registrations["material_asset"].executor._service is service
    assert runtime.scheduler.database_path == repository.database_path.resolve()
    assert runtime.scheduler.lock_path.parent == repository.database_path.resolve().parent


def test_interactive_permit_survives_when_scheduled_material_is_off(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ProductionRepository(tmp_path / "production.sqlite", tmp_path / "library")
    monkeypatch.setattr(main_module, "production_repo", repository)
    monkeypatch.setattr(main_module, "material_execution_mode", "interactive")
    for name, value in _CALIBRATED_ENV.items():
        monkeypatch.setenv(name, value)

    runtime, service = main_module._build_device_components(repository=repository)

    # 定时触发关着，交互式许可照样在，且仍然只有这一个运行时。
    assert set(runtime.registrations) == {"creation_job", "platform_publish"}
    assert service is None


def test_uncalibrated_budget_refuses_to_build_a_runtime(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有真实标定值就明确拒绝建造，不猜预算、不放一个永远返回 None 的假探针。"""
    repository = ProductionRepository(tmp_path / "production.sqlite", tmp_path / "library")
    monkeypatch.setattr(main_module, "production_repo", repository)
    for name in _CALIBRATED_ENV:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(HostBudgetUncalibrated, match="尚未标定"):
        main_module._build_device_components(repository=repository)
