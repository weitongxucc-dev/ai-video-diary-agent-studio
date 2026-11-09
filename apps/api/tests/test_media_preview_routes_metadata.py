"""Persisted preview HTTP reads and refusal gates without runtime or media execution."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.creation_execution import CreationExecutionActor
from app.local_members import LocalMemberService
from app.production import ProductionRepository
from app.profiles.preview_contracts import MediaPreviewRunRecord


def test_metadata_preview_reads_filter_before_latest_and_refusals_do_not_write(tmp_path, monkeypatch):
    runtime_calls = []

    def forbidden_runtime(*args, **kwargs):
        runtime_calls.append(True)
        pytest.fail("metadata preview reads/refusals must not construct or access runtime")

    # Install the actual runtime boundary before constructing or seeding any owner.
    monkeypatch.setattr(main, "_creation_runtime", forbidden_runtime)
    monkeypatch.setattr(main, "_build_device_components", forbidden_runtime)
    monkeypatch.setattr(main, "_start_device_runtime", forbidden_runtime)
    members = LocalMemberService(tmp_path / "members.sqlite3")
    owner, token = members.whoami(None)
    other, other_token = members.join(members.create_invite_code(), "另一成员")
    repository = ProductionRepository(tmp_path / "preview.sqlite3", tmp_path / "library")
    monkeypatch.setattr(main, "local_members_service", members)
    monkeypatch.setattr(main, "production_repo", repository)
    monkeypatch.setattr(main, "auth_context", SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, "current_workspace_id",
                        lambda request: request.headers.get("x-test-workspace", "local"))
    when = datetime(2026, 10, 29, tzinfo=timezone.utc)
    first = MediaPreviewRunRecord(
        id="attempt-1", workspace_id="local", preview_type="voice", identity="shared-identity",
        status="failed", capability_id="voice_clone", model_id="metadata-only", language="zh",
        person_profile_id=None, person_version=None, voice_profile_id="voice", voice_version="v1",
        authorization_id=None, authorization_version=None, started_at=when,
        finished_at=when + timedelta(seconds=1), reason="metadata_fixture",
        impact="未执行预览", repair_action="仅用于元数据读取验证",
        member_identity=CreationExecutionActor("local", owner.member_id).identity_payload(),
    )
    records = [first,
        replace(first, id="attempt-2", attempt=2, finished_at=when + timedelta(seconds=2)),
        replace(first, id="other-attempt-3", attempt=3, finished_at=when + timedelta(seconds=3),
                member_identity=CreationExecutionActor("local", other.member_id).identity_payload()),
        replace(first, id="terminal", identity="terminal", finished_at=when + timedelta(seconds=4)),
    ]
    for record in records:
        repository.preview_records.save_media_preview_run(record)

    def snapshot():
        with repository._connect() as conn:
            return [tuple(row) for row in conn.execute("SELECT * FROM media_preview_attempts ORDER BY id")]

    before = snapshot()
    client = TestClient(main.app)
    headers = {"X-Local-Member-Token": token}
    other_headers = {"X-Local-Member-Token": other_token}
    base = "/api/media/previews"
    own = client.get(base, headers=headers)
    assert own.status_code == 200
    assert [run["id"] for run in own.json()["items"]] == ["terminal", "attempt-2"]
    assert own.json()["total_count"] == 2
    assert all(run["output_url"] is None for run in own.json()["items"])
    assert [run["id"] for run in client.get(base, headers=other_headers).json()["items"]] == ["other-attempt-3"]
    attempts = client.get(base + "/voice/shared-identity/attempts", headers=headers)
    assert attempts.status_code == 200
    assert [run["id"] for run in attempts.json()["items"]] == ["attempt-2", "attempt-1"]
    assert attempts.json()["total_count"] == 2
    assert client.get(base, headers={**headers, "x-test-workspace": "foreign"}).json() == {"items": [], "total_count": 0}
    assert client.post(base + "/voice/terminal/cancel", headers=headers).status_code == 409
    assert client.post(base + "/voice/terminal/cancel", headers=other_headers).status_code == 404
    assert client.get(base + "/voice/terminal/media", headers=headers).status_code == 404
    assert client.get(base + "/voice/missing/attempts", headers=headers).status_code == 404
    assert client.get(base + "/voice/missing/media", headers=headers).status_code == 404
    assert runtime_calls == []
    assert snapshot() == before
    reopened = ProductionRepository(repository.database_path, repository.video_library_path)
    assert reopened.preview_records.get_media_preview_run("local", "voice", "terminal") == records[-1]
