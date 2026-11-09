"""Source creation uses the actual HTTP boundary and isolated persistent owner."""
from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import app.main as main
from test_source_task_use_case import source_case as source_case  # noqa: F401


def test_source_task_http_replay_roundtrip_and_boundary_validation(source_case, monkeypatch):
    s = source_case
    monkeypatch.setattr(main, "production_repo", s.repo)
    monkeypatch.setattr(main, "auth_context", SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, "_require_permit_actor", lambda request, workspace: s.actor)
    monkeypatch.setattr(main, "_verify_creation_identity", s.verify)
    client = TestClient(main.app)
    payload = asdict(s.command)
    payload["expected_asset_updated_at"] = s.command.expected_asset_updated_at.isoformat()
    response = client.post("/api/source-tasks", json=payload)
    assert response.status_code == 201, response.text
    receipt = response.json()
    task = s.repo.task_records.get_creation_task("local", receipt["task_id"])
    plan = s.repo.plan_records.get_creation_plan("local", task.plan_id)
    assert plan.source_media == receipt["source_media"]
    assert plan.duration_target_seconds == 9.25 and task.confirmed_script_id is None
    assert main._creation_task_response_adapter().creation_plan_preview_response(plan).source_media == plan.source_media
    assert client.get("/api/source-tasks/" + payload["request_id"]).json() == receipt
    s.path.unlink()
    assert client.post("/api/source-tasks", json=payload).json() == receipt
    assert client.post("/api/source-tasks", json={**payload, "preserve_audio": False}).status_code == 409
    assert client.get("/api/source-tasks/" + str(uuid4())).status_code == 404
    assert client.post("/api/source-tasks", json={**payload, "preserve_audio": "false"}).status_code == 422
    assert client.post("/api/source-tasks", json={**payload, "source_path": "/arbitrary/local/file"}).status_code == 422


def test_source_task_http_identity_unavailable_does_not_create(source_case, monkeypatch):
    s = source_case
    monkeypatch.setattr(main, "production_repo", s.repo)
    monkeypatch.setattr(main, "_require_permit_actor", lambda request, workspace: s.actor)
    monkeypatch.setattr(main, "_verify_creation_identity", lambda actor: "unknown")
    payload = asdict(s.command)
    payload["expected_asset_updated_at"] = s.command.expected_asset_updated_at.isoformat()
    response = TestClient(main.app).post("/api/source-tasks", json=payload)
    assert response.status_code == 503, response.text
    assert s.use_case.read(s.command.request_id, s.actor) is None
    assert not s.repo.task_records.list_creation_tasks("local")
