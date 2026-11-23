"""Rebinding roots is an owner-only local-member write."""

from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.local_members import LocalMemberService
from app.material_ingestion import MaterialIngestionService
from app.production import ProductionRepository


client = TestClient(main_module.app)
WORKSPACE = "ws-material-rebind-api"


def _setup(tmp_path, monkeypatch):
    members = LocalMemberService(tmp_path / "members.sqlite3")
    old = tmp_path / "old-library"
    new = tmp_path / "new-library"
    old.mkdir()
    new.mkdir()
    repository = ProductionRepository(tmp_path / "production.sqlite3", old)
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "production_repo", repository)
    monkeypatch.setattr(main_module, "material_ingestion", MaterialIngestionService(repository))
    monkeypatch.setattr(main_module, "material_execution_mode", "legacy")
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: WORKSPACE)
    owner, owner_token = members.whoami(None)
    member, member_token = members.join(members.create_invite_code(), "普通成员")
    bind = client.post(
        "/api/production/materials/intake/bind",
        json={"expected_directory": str(old)},
        headers={"X-Local-Member-Token": owner_token},
    )
    assert bind.status_code == 200, bind.text
    return old, new, owner_token, member_token


def _payload(old: Path, new: Path, operation_id: str = "guard-1") -> dict[str, str]:
    return {
        "old_directory": str(old),
        "new_directory": str(new),
        "operation_id": operation_id,
    }


def test_rebind_prepare_requires_owner_for_local_member_tokens(tmp_path, monkeypatch):
    old, new, owner_token, member_token = _setup(tmp_path, monkeypatch)
    response = client.post(
        "/api/production/materials/intake/rebind/prepare",
        json=_payload(old, new),
        headers={"X-Local-Member-Token": member_token},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "仅所有者可执行此操作"

    prepared = client.post(
        "/api/production/materials/intake/rebind/prepare",
        json=_payload(old, new),
        headers={"X-Local-Member-Token": owner_token},
    )
    assert prepared.status_code == 200, prepared.text
    committed = client.post(
        "/api/production/materials/intake/rebind/commit",
        json={
            "operation_id": "guard-1",
            "manifest_sha256": prepared.json()["manifest_sha256"],
        },
        headers={"X-Local-Member-Token": member_token},
    )
    assert committed.status_code == 403
    assert committed.json()["detail"] == "仅所有者可执行此操作"


def test_rebind_prepare_rejects_unknown_token_and_allows_owner(tmp_path, monkeypatch):
    old, new, owner_token, _member_token = _setup(tmp_path, monkeypatch)
    unknown = client.post(
        "/api/production/materials/intake/rebind/prepare",
        json=_payload(old, new, "guard-unknown"),
        headers={"X-Local-Member-Token": "unknown-token"},
    )
    assert unknown.status_code == 401

    allowed = client.post(
        "/api/production/materials/intake/rebind/prepare",
        json=_payload(old, new),
        headers={"X-Local-Member-Token": owner_token},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "prepared"
