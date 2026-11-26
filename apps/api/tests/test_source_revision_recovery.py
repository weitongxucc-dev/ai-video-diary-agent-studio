from dataclasses import asdict, replace
from datetime import datetime, timedelta
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.creation.source_task_use_case import SourceRevisionRecoveryCommand
from app.materials.discovery_store import DiscoveryStore
from test_source_task_use_case import source_case as source_case  # noqa: F401


def prepare_recovery(case, receipt, *, digest=None, receive=True):
    task = case.repo.task_records.get_creation_task("local", receipt["task_id"])
    version = case.repo.timeline_records.get_timeline_version("local", task.current_timeline_version_id)
    signature = case.path.stat()
    if receive:
        store = DiscoveryStore(case.repo._connect)
        store.save_receipt("local", case.actor.member_id, digest or hashlib.sha256(case.path.read_bytes()).hexdigest(), {
            "path": str(case.path), "asset_id": case.asset.id,
            "revision": [signature.st_dev, signature.st_ino, signature.st_size, signature.st_mtime_ns, signature.st_ctime_ns],
        })
    material = case.repo.get_asset("local", case.asset.id)
    material.updated_at += timedelta(seconds=1)
    case.repo._save_asset(material)
    current = case.repo.get_asset("local", case.asset.id)
    command = SourceRevisionRecoveryCommand(str(uuid4()), version.id, version.sequence, current.id,
        datetime.fromisoformat(receipt["source_media"]["asset_updated_at"]), current.updated_at)
    return task, version, command


@pytest.fixture
def recovery_http(source_case, monkeypatch):
    case = source_case
    monkeypatch.setattr(main, "production_repo", case.repo)
    monkeypatch.setattr(main, "auth_context", SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, "_require_permit_actor", lambda request, workspace: case.actor)
    monkeypatch.setattr(main, "_verify_creation_identity", case.verify)
    return case, TestClient(main.app)


def recover_payload(command):
    return {**asdict(command),
        "expected_bound_asset_updated_at": command.expected_bound_asset_updated_at.isoformat(),
        "expected_asset_updated_at": command.expected_asset_updated_at.isoformat()}


def test_recovery_roundtrips_without_changing_the_editable_version_or_original_receipt(recovery_http):
    case, client = recovery_http
    original_receipt = case.use_case.create(case.command, case.actor)
    task, version, command = prepare_recovery(case, original_receipt)
    plan = case.repo.plan_records.get_creation_plan("local", task.plan_id)
    route = f"/api/creation-tasks/{task.id}/source-revision-recoveries"
    payload = recover_payload(command)
    assert client.get(f"{route}/{command.request_id}").status_code == 404
    assert client.post(route, json={**payload, "expected_timeline_sequence": 2}).status_code == 409
    assert client.post(route, json={**payload, "source_end_seconds": 30}).status_code == 422
    assert client.post(route, json=payload, headers={"X-Assistant-Input-ID": str(uuid4())}).status_code == 409
    saved = client.post(route, json=payload)
    assert saved.status_code == 200, saved.text
    outcome = saved.json()
    assert outcome["timeline_version_id"] == version.id and outcome["timeline_sequence"] == version.sequence
    assert outcome["asset_updated_at"] == command.expected_asset_updated_at.isoformat()
    updated_plan = case.repo.plan_records.get_creation_plan("local", plan.id)
    assert updated_plan == replace(plan, updated_at=updated_plan.updated_at,
        source_media={**plan.source_media, "asset_updated_at": outcome["asset_updated_at"]})
    assert case.repo.timeline_records.list_timeline_versions("local", task.id) == [version]
    assert case.repo.task_records.get_creation_task("local", task.id) == task
    assert case.use_case.read(case.command.request_id, case.actor) == original_receipt
    case.path.unlink()
    assert client.get(f"{route}/{command.request_id}").json() == outcome
    assert client.post(route, json=payload).json() == outcome
    assert client.post(route, json={**payload, "expected_timeline_sequence": 2}).status_code == 409
    assert len(case.repo.execution_records.list_task_events("local", task.id)) == 2


@pytest.mark.parametrize("change", ["missing_proof", "different_digest", "revoke_use", "replace_file", "advance_head", "remove_member"])
def test_recovery_refuses_without_proven_current_bytes_and_writer_facts(recovery_http, monkeypatch, change):
    case, client = recovery_http
    original = case.use_case.create(case.command, case.actor)
    task, version, command = prepare_recovery(case, original, receive=change != "missing_proof",
        digest="0" * 64 if change == "different_digest" else None)

    def change_after_hash(actor):
        case.verify(actor)
        if change == "revoke_use":
            material = case.repo.get_asset("local", case.asset.id)
            material.use_state = "excluded"
            case.repo._save_asset(material)
        elif change == "replace_file":
            case.path.write_bytes(b"x" * case.path.stat().st_size)
        elif change == "advance_head":
            case.repo.timeline_records.create_timeline_version(replace(version, id="advanced", sequence=2,
                parent_version_id=version.id), expected_current_version_id=version.id)
        elif change == "remove_member":
            case.members.remove_member(actor.member_id)
        return "ready"

    route = f"/api/creation-tasks/{task.id}/source-revision-recoveries"
    monkeypatch.setattr(main, "_verify_creation_identity", change_after_hash)
    response = client.post(route, json=recover_payload(command))
    assert response.status_code == (403 if change == "remove_member" else 409), response.text
    plan = case.repo.plan_records.get_creation_plan("local", task.plan_id)
    assert plan.source_media == original["source_media"]
    assert [event.event_type for event in case.repo.execution_records.list_task_events("local", task.id)] == [
        "source_task_created"]


def test_other_member_cannot_recover_visible_work_or_read_its_receipt(recovery_http, monkeypatch):
    case, client = recovery_http
    original = case.use_case.create(case.command, case.actor)
    task, _, command = prepare_recovery(case, original)
    case.use_case.recover_revision(task.id, command, case.actor)
    actor = replace(case.actor, member_id=case.members.add_member("another member").member_id)
    monkeypatch.setattr(main, "_require_permit_actor", lambda request, workspace: actor)
    route = f"/api/creation-tasks/{task.id}/source-revision-recoveries"
    assert client.get(f"{route}/{command.request_id}").status_code == 404
    assert client.post(route, json=recover_payload(command)).status_code == 403
