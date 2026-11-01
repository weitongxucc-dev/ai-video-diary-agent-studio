"""WIL-126 HTTP wiring, isolated local-mode workspace; no member-permission claim."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

import app.main as main_module
from app.material_ingestion import MaterialIngestionService
from app.local_members import LocalMemberService
from app.production import ProductionRepository


def material_intake_no_execution(monkeypatch):
    """Only controlled tmp metadata and opaque copies; never real work or roots."""
    import subprocess
    import wave
    from app.materials import discovery as discovery_module

    violations = []

    def forbidden(*_args, **_kwargs):
        violations.append("execution outside intake HTTP metadata scope")
        raise AssertionError(violations[-1])

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(wave, "open", forbidden)
    for name in ("run_plan_turn", "run_task_turn", "_start_device_runtime", "_creation_runtime"):
        monkeypatch.setattr(main_module, name, forbidden)
    for name in ("start", "_run", "discover_and_submit_hashes"):
        monkeypatch.setattr(MaterialIngestionService, name, forbidden)
    monkeypatch.setattr(ProductionRepository, "analyze_asset", forbidden)
    monkeypatch.setattr(discovery_module, "system_roots", forbidden)
    # The constructor's default provider is a bound function object, so patch it
    # too. The discovery node supplies its explicit tmp-only root_provider.
    constructor = discovery_module.MaterialDiscoveryService.__init__
    monkeypatch.setattr(constructor, "__kwdefaults__", {
        **constructor.__kwdefaults__, "root_provider": forbidden,
    })
    return violations


@pytest.fixture
def intake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    library = tmp_path / "materials"
    library.mkdir()
    duration = Mock(return_value=1.0)
    repo = ProductionRepository(
        tmp_path / "production.sqlite", library,
        duration_reader=duration, scene_reader=lambda path, seconds: [],
    )
    service = MaterialIngestionService(repo)
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "material_ingestion", service)
    monkeypatch.setattr(main_module, "material_execution_mode", "legacy")
    monkeypatch.setattr(main_module, "active_workspace_id", lambda: "one")
    members = LocalMemberService(tmp_path / "members.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", members)
    _, token = members.whoami(None)
    return TestClient(main_module.app, headers={"X-Local-Member-Token": token}), repo, service, duration


@pytest.fixture
def metadata_intake(monkeypatch, request):
    violations = material_intake_no_execution(monkeypatch)
    # Install guards before constructing even the original isolated repository.
    yield request.getfixturevalue("intake")
    assert violations == []


def bind(client: TestClient, repo: ProductionRepository) -> None:
    response = client.post("/api/production/materials/intake/bind", json={
        "expected_directory": str(repo.video_library_path),
    })
    assert response.status_code == 200, response.text


def test_external_pending_list_confirmation_and_readback(intake) -> None:
    client, repo, service, duration = intake
    path = repo.video_library_path / "external.mp4"
    path.write_bytes(b"fully copied")
    bind(client, repo)
    scanned = client.post("/api/production/materials/intake/scan")
    assert scanned.status_code == 200 and scanned.json()["assets"] == []
    status = client.get("/api/production/materials/intake").json()
    pending = status["pending_files"][0]
    assert pending == {"relative_path": "external.mp4", "expected_revision": repo.material_source_files.material_copy_revision(path.stat())}
    assert duration.call_count == 0
    service._wake.clear()
    response = client.post("/api/production/materials/intake/copy-complete", json={
        **pending, "confirmation_source": "manual",
    })
    assert response.status_code == 200, response.text
    assert response.json()["confirmed"] is True
    assert response.json()["intake"]["pending_files"] == []
    assert service._wake.is_set()
    assert duration.call_count == 0  # HTTP confirmation itself does not validate media.
    assert client.get("/api/production/materials/intake").json()["pending_files"] == []
    with sqlite3.connect(repo.database_path) as conn:
        assert conn.execute("SELECT workspace_id, confirmation_source FROM material_ingestion_files").fetchall() == [("one", "manual")]


def test_stale_revision_rejected_and_foreign_binding_not_exposed(metadata_intake, monkeypatch: pytest.MonkeyPatch) -> None:
    client, repo, service, duration = metadata_intake
    path = repo.video_library_path / "external.mp4"
    path.write_bytes(b"old")
    bind(client, repo)
    client.post("/api/production/materials/intake/scan")
    pending = client.get("/api/production/materials/intake").json()["pending_files"][0]
    path.write_bytes(b"new revision")
    service._wake.clear()
    response = client.post("/api/production/materials/intake/copy-complete", json={**pending, "confirmation_source": "manual"})
    assert response.status_code == 409
    assert "文件修订已变化" in response.json()["detail"]
    assert not service._wake.is_set()
    updated = client.get("/api/production/materials/intake").json()["pending_files"][0]
    assert updated["expected_revision"] == repo.material_source_files.material_copy_revision(path.stat())
    assert duration.call_count == 0
    malformed = {**updated, "expected_revision": {**updated["expected_revision"], "mtime_ns": 9007199254740999}, "confirmation_source": "manual"}
    assert client.post("/api/production/materials/intake/copy-complete", json=malformed).status_code == 422
    monkeypatch.setattr(main_module, "active_workspace_id", lambda: "other")
    status = client.get("/api/production/materials/intake").json()
    assert status["pending_files"] == [] and status["configured_directory"] is None
    response = client.post("/api/production/materials/intake/copy-complete", json={**updated, "confirmation_source": "manual"})
    assert response.status_code == 409 and "未绑定" in response.json()["detail"]


def test_committed_app_copy_confirms_before_discovery_and_scan_receives_it(intake) -> None:
    client, repo, service, duration = intake
    bind(client, repo)
    path = repo.video_library_path / "app.mp4"
    payload = b"committed app copy"
    path.write_bytes(payload)
    response = client.post("/api/production/materials/intake/copy-complete", json={
        "relative_path": "app.mp4", "expected_revision": repo.material_source_files.material_copy_revision(path.stat()),
        "confirmation_source": "app_import",
    })
    assert response.status_code == 200 and response.json()["confirmed"] is True
    assert service._wake.is_set()
    scanned = client.post("/api/production/materials/intake/scan")
    assert scanned.status_code == 200, scanned.text
    assert len(scanned.json()["assets"]) == 1
    assert scanned.json()["assets"][0]["analysis_status"] == "ready"
    assert duration.call_count == 1 and path.read_bytes() == payload


def test_confirmed_member_copy_is_not_readable_without_identity_outside_local(intake) -> None:
    client, repo, service, duration = intake
    bind(client, repo)
    source = repo.video_library_path / "member.mp4"
    source.write_bytes(b"complete member copy")
    confirmed = client.post("/api/production/materials/intake/copy-complete", json={
        "relative_path": source.name, "expected_revision": repo.material_source_files.material_copy_revision(source.stat()),
        "confirmation_source": "app_import",
    })
    assert confirmed.status_code == 200, confirmed.text
    scanned = client.post("/api/production/materials/scan")
    assert scanned.status_code == 200, scanned.text
    asset_id = scanned.json()["assets"][0]["id"]
    anonymous = TestClient(main_module.app)
    assert anonymous.get(f"/api/production/materials/{asset_id}").status_code == 404
    assert anonymous.get(f"/api/production/materials/{asset_id}/media").status_code == 404
    assert anonymous.get("/api/production/materials").json()["total_count"] == 0
    assert anonymous.post("/api/production/materials/scan").json()["assets"] == []
    assert anonymous.post("/api/production/materials/intake/scan").json()["assets"] == []
    invalid = client.get("/api/production/materials/intake", headers={"X-Local-Member-Token": "invalid"})
    assert invalid.status_code == 401
