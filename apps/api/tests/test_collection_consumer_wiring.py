"""Actual collection/API consumers share the neutral persistence helpers."""
from fastapi.testclient import TestClient

import app.main as main
from app import persistence_json, production, profile_preview_execution
from app.local_members import LocalMemberService
from app.materials.contracts import MaterialCollection


def catalog_metadata_no_execution(monkeypatch):
    """Collection/name facts use SQLite and opaque metadata, never execution."""
    import subprocess
    import wave

    violations = []

    def forbidden(*_args, **_kwargs):
        violations.append("catalog HTTP metadata must not execute work")
        raise AssertionError(violations[-1])

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(wave, "open", forbidden)
    for name in ("run_plan_turn", "run_task_turn", "_start_device_runtime", "_creation_runtime"):
        monkeypatch.setattr(main, name, forbidden)
    monkeypatch.setattr(production.ProductionRepository, "scan_library", forbidden)
    monkeypatch.setattr(production.ProductionRepository, "analyze_asset", forbidden)
    return violations


def catalog_member_headers(tmp_path, monkeypatch):
    members = LocalMemberService(tmp_path / "members.sqlite3")
    _owner, token = members.whoami(None)
    monkeypatch.setattr(main, "local_members_service", members)
    return {"X-Local-Member-Token": token}


def test_production_and_preview_reference_same_neutral_serialization_objects():
    for name in ("_json_payload", "_json_object", "_json_default", "_with_datetime"):
        assert getattr(production, name) is getattr(persistence_json, name)
    assert profile_preview_execution._json_payload is persistence_json._json_payload


def test_real_collection_http_hierarchy_and_reopen_use_unique_store(tmp_path, monkeypatch):
    violations = catalog_metadata_no_execution(monkeypatch)
    headers = catalog_member_headers(tmp_path, monkeypatch)
    repo = production.ProductionRepository(tmp_path / "fixture.sqlite3", tmp_path / "library")
    monkeypatch.setattr(main, "production_repo", repo)
    monkeypatch.setattr(main, "current_workspace_id", lambda _: "collection-test")
    client = TestClient(main.app, headers=headers)
    parent = client.post("/api/production/material-collections", json={"name": "素材"})
    assert parent.status_code == 200, parent.text
    child = client.post("/api/production/material-collections", json={
        "name": "校园", "parent_id": parent.json()["id"],
    })
    assert child.status_code == 200, child.text
    assert child.json()["parent_id"] == parent.json()["id"]
    listed = client.get("/api/production/material-collections")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["collections"]] == [child.json()["id"], parent.json()["id"]]
    rebuilt = production.ProductionRepository(repo.database_path, tmp_path / "library")
    assert [item.id for item in rebuilt.list_collections("collection-test")] == [child.json()["id"], parent.json()["id"]]
    assert rebuilt.list_collections("another-workspace") == []

    foreign_parent = repo.save_collection(MaterialCollection(
        id="foreign-parent", workspace_id="another-workspace", name="别区父集合",
    ))
    refused = client.post("/api/production/material-collections", json={
        "name": "不能写入", "parent_id": foreign_parent.id,
    })
    assert refused.status_code == 404
    assert [item.id for item in rebuilt.list_collections("collection-test")] == [child.json()["id"], parent.json()["id"]]
    assert violations == []
