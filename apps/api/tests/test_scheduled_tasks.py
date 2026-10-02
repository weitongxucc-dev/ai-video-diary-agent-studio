"""Scheduled-task data model + CRUD (first slice; no execution wiring)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.database_migrations import PRODUCTION_SCHEMA_VERSION
from app.production import ProductionRepository
from app.scheduled_tasks import (
    ScheduledTaskCreateRequest,
    ScheduledTaskEnableRequest,
    ScheduledTaskService,
    ScheduledTaskUpdateRequest,
)


def _service(tmp_path: Path) -> ScheduledTaskService:
    repo = ProductionRepository(tmp_path / "production.sqlite3", tmp_path / "library")
    return ScheduledTaskService(repo.database_path)


def _now() -> datetime:
    return datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)


def test_migration_creates_scheduled_tasks_table(tmp_path):
    service = _service(tmp_path)
    with service._connect() as conn:
        versions = [row[0] for row in conn.execute(
            "SELECT version FROM production_schema_migrations ORDER BY version").fetchall()]
        assert versions == list(range(1, PRODUCTION_SCHEMA_VERSION + 1))
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_tasks'"
        ).fetchone()
    assert table is not None


def test_create_list_and_workspace_scoping(tmp_path):
    service = _service(tmp_path)
    created = service.create_task("workspace_a", ScheduledTaskCreateRequest(
        task_type="material_analysis", trigger_mode="idle", target_ref="asset:1"))
    assert created.enabled is True
    assert service.list_tasks("workspace_a") == [created]
    assert service.list_tasks("workspace_b") == []
    assert service.get_task("workspace_a", created.id) == created


def test_scheduled_mode_requires_not_before_utc(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="最早开始时间"):
        service.create_task("workspace_a", ScheduledTaskCreateRequest(
            task_type="creation_render", trigger_mode="scheduled", target_ref="task:1"))


def test_route_rejects_unknown_task_type(tmp_path, monkeypatch):
    service = _service(tmp_path)
    monkeypatch.setattr(main_module, "scheduled_task_service", service)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "workspace_a")
    client = TestClient(main_module.app)
    assert client.post("/api/scheduled-tasks", json={
        "task_type": "bogus", "trigger_mode": "idle", "target_ref": "x",
    }).status_code == 422


def test_update_enable_disable_delete(tmp_path):
    service = _service(tmp_path)
    created = service.create_task("workspace_a", ScheduledTaskCreateRequest(
        task_type="material_analysis", trigger_mode="idle", target_ref="asset:1"))
    updated = service.update_task("workspace_a", created.id, ScheduledTaskUpdateRequest(
        task_type="creation_render", trigger_mode="scheduled", not_before_utc=_now()))
    assert updated.task_type == "creation_render"
    assert updated.trigger_mode == "scheduled"
    assert updated.not_before_utc == _now().isoformat()
    assert service.set_enabled("workspace_a", created.id, False).enabled is False
    assert service.set_enabled("workspace_a", created.id, True).enabled is True
    service.delete_task("workspace_a", created.id)
    assert service.list_tasks("workspace_a") == []
    with pytest.raises(KeyError):
        service.delete_task("workspace_a", created.id)


def test_update_rejects_scheduled_without_not_before_utc(tmp_path):
    service = _service(tmp_path)
    created = service.create_task("workspace_a", ScheduledTaskCreateRequest(
        task_type="material_analysis", trigger_mode="idle", target_ref="asset:1"))
    with pytest.raises(ValueError, match="最早开始时间"):
        service.update_task("workspace_a", created.id, ScheduledTaskUpdateRequest(
            trigger_mode="scheduled"))


def test_routes_list_create_update_enable_delete(tmp_path, monkeypatch):
    service = _service(tmp_path)
    monkeypatch.setattr(main_module, "scheduled_task_service", service)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "workspace_a")
    client = TestClient(main_module.app)

    assert client.get("/api/scheduled-tasks").json() == {"tasks": [], "total_count": 0}
    created = client.post("/api/scheduled-tasks", json={
        "task_type": "material_analysis", "trigger_mode": "idle", "target_ref": "asset:1",
    })
    assert created.status_code == 201
    task_id = created.json()["id"]

    assert client.get("/api/scheduled-tasks").json()["total_count"] == 1
    updated = client.put(f"/api/scheduled-tasks/{task_id}", json={
        "task_type": "creation_render",
        "trigger_mode": "scheduled",
        "not_before_utc": _now().isoformat(),
    })
    assert updated.status_code == 200
    assert updated.json()["task_type"] == "creation_render"

    disabled = client.patch(
        f"/api/scheduled-tasks/{task_id}/enabled",
        json=ScheduledTaskEnableRequest(enabled=False).model_dump(),
    )
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False

    assert client.delete(f"/api/scheduled-tasks/{task_id}").status_code == 204
    assert client.get("/api/scheduled-tasks").json()["total_count"] == 0
    assert client.delete(f"/api/scheduled-tasks/{task_id}").status_code == 404


def test_route_rejects_invalid_payload(tmp_path, monkeypatch):
    service = _service(tmp_path)
    monkeypatch.setattr(main_module, "scheduled_task_service", service)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "workspace_a")
    client = TestClient(main_module.app)
    assert client.post("/api/scheduled-tasks", json={
        "task_type": "material_analysis", "trigger_mode": "scheduled", "target_ref": "x",
    }).status_code == 400
