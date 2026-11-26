"""The production preview verifies saved canvas limits without running media."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app import main
from app.artifact_store import ArtifactStore
from app.creation.render_specification import render_target
from app.device_resource_configuration import backend_identity, current_device_identity
from app.host_resource_probes import CpuAudioBudget
from app.model_runtime import ModelCatalog
from test_device_resource_profile import profile_document, write_profile
from test_source_execution_requirements import database_snapshot, prepared_source
from test_source_task_use_case import source_case as source_case


def write_render_profile(root, settings, *, width, height, recorded=True, audio_input_cap=None):
    document = profile_document()
    document["device"] = asdict(current_device_identity({"DEVICE_ID": "render-spec-fixture"}))
    budget = document["budget"]["cpu_render"]
    budget.update(max_width_px=width, max_height_px=height)
    operations = ["cpu_render"]
    if audio_input_cap is not None:
        document["budget"]["cpu_audio"] = asdict(CpuAudioBudget(
            2, audio_input_cap, 30, 30, 48000, 2, 32 * 1024**2, 1, 30, 1024**2, 16, .1, 2 * 1024**2, 1024**2))
        operations.append("cpu_audio")
    for operation in operations:
        identity = backend_identity(operation, None, settings=settings)
        assert identity
        document["backends"][operation] = identity
        if not recorded:
            continue
        budget = document["budget"][operation]
        receipt = {"format_version": 1, "device": document["device"], "backend_identity": identity,
            "budget_digest": hashlib.sha256(json.dumps(budget, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "method": "synthetic profile binding test", "input_range": {"operation": operation},
            "observations": {"process_peak_rss_bytes": 100}}
        raw = json.dumps(receipt).encode()
        receipts = root / "resource-profile-evidence"
        receipts.mkdir(exist_ok=True)
        record_id = "render" if operation == "cpu_render" else "audio"
        (receipts / f"{record_id}.json").write_bytes(raw)
        document.setdefault("evidence", {})[operation] = {"record_id": record_id, "sha256": hashlib.sha256(raw).hexdigest()}
    return write_profile(root, document)


@pytest.mark.parametrize(("state", "width", "height", "expected"), [
    ("recorded", 1280, 720, "verified"),
    ("recorded", 1920, 1080, "verified"),
    ("changed_backend", 1920, 1080, "stale"),
    ("deleted_receipt", 1920, 1080, "unknown"),
    ("declared", 1920, 1080, "unknown"),
    ("missing", 0, 0, "missing"),
])
def test_execution_preview_reads_only_bound_saved_canvas_profile(
    source_case, tmp_path, monkeypatch, state, width, height, expected,
):
    case = source_case
    task, _ = prepared_source(case)
    for tool in ("ffmpeg", "ffprobe"):
        executable = tmp_path / tool
        executable.write_text("#!/bin/sh\nexit 123\n")
        executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("DEVICE_ID", "render-spec-fixture")
    monkeypatch.delenv("YAOHE_TEMPLATE_RENDERER", raising=False)
    path = tmp_path / "device-resource-profile.json"
    settings = replace(main.settings, device_resource_profile_path=str(path), device_resource_mode="normal")
    if state != "missing":
        path = write_render_profile(tmp_path, settings, width=width, height=height, recorded=state != "declared")
    if state == "changed_backend":
        (tmp_path / "ffmpeg").write_text("#!/bin/sh\nexit 124\n")
    elif state == "deleted_receipt":
        (tmp_path / "resource-profile-evidence" / "render.json").unlink()

    def forbidden(*args, **kwargs):
        pytest.fail("production preview started a device, model, or execution read outside saved facts")

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "auth_context", SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, "local_members_service", case.members)
    monkeypatch.setattr(main, "production_repo", case.repo)
    monkeypatch.setattr(main, "artifact_store", ArtifactStore(tmp_path / "artifacts"))
    monkeypatch.setattr(main, "_creation_runtime", forbidden)
    monkeypatch.setattr(main, "_device_operation_budget", forbidden)
    monkeypatch.setattr(ModelCatalog, "status", forbidden)
    before = database_snapshot(case.repo)
    archives_before = {file.relative_to(tmp_path): file.read_bytes() for file in tmp_path.rglob("*.json")}

    result = TestClient(main.app).get(f"/api/creation-tasks/{task.id}/execution/preview")

    assert result.status_code == 200, result.text
    data = result.json()
    profile = {"status": expected}
    if expected == "verified":
        profile.update(max_width_px=width, max_height_px=height)
    assert data["render_specification"] == {"width_px": 1920, "height_px": 1080, "profile": profile}
    assert data["requirements"]["can_continue"] is True
    assert database_snapshot(case.repo) == before
    assert {file.relative_to(tmp_path): file.read_bytes() for file in tmp_path.rglob("*.json")} == archives_before


def test_render_target_uses_adopted_montage_and_keeps_ambiguous_budget_unknown(source_case):
    task, plan = prepared_source(source_case)
    with source_case.repo.execution_inputs.creation_execution_read() as connection:
        size, operation, _ = render_target(source_case.repo, task, replace(plan, aspect_ratio="9:16"), connection=connection)
        assert size == (1920, 1080) and operation == "cpu_render"
        size, operation, _ = render_target(source_case.repo, task, replace(plan, presenter_share_target=30), connection=connection)
        assert size == (1920, 1080) and operation is None


@pytest.mark.parametrize(("source_bytes", "condition", "verified"), [
    (25_290_553, "current", True),
    (1_162_731, "current", True),
    (25_290_553, "stale_audio_profile", False),
    (25_290_553, "unrecorded_audio_profile", False),
    (25_290_553, "source_replaced", False),
    (25_290_553, "other_actor", False),
    (25_290_553, "no_original_audio", False),
])
def test_execution_preview_compares_the_complete_authorized_original_audio_source(
    source_case, tmp_path, monkeypatch, source_bytes, condition, verified,
):
    case = source_case
    with case.path.open("r+b") as source:
        source.truncate(source_bytes)
    asset = case.repo._save_asset(replace(case.asset, size_bytes=source_bytes,
        modified_at=datetime.fromtimestamp(case.path.stat().st_mtime, timezone.utc), duration_seconds=30))
    case.command = replace(case.command, source_end_seconds=6, expected_asset_updated_at=asset.updated_at,
        preserve_audio=condition != "no_original_audio")
    task, plan = prepared_source(case)
    for tool in ("ffmpeg", "ffprobe"):
        executable = tmp_path / tool
        executable.write_bytes(b"identity test fixture")
        executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("DEVICE_ID", "render-spec-fixture")
    monkeypatch.delenv("YAOHE_TEMPLATE_RENDERER", raising=False)
    path = tmp_path / "device-resource-profile.json"
    settings = replace(main.settings, device_resource_profile_path=str(path), device_resource_mode="normal")
    cap = 23_171_072
    write_render_profile(tmp_path, settings, width=1920, height=1080, audio_input_cap=cap)
    if condition in {"stale_audio_profile", "unrecorded_audio_profile"}:
        document = json.loads(path.read_bytes())
        if condition == "stale_audio_profile":
            document["backends"]["cpu_audio"] = "old-audio-backend"
        else:
            del document["evidence"]["cpu_audio"]
        write_profile(tmp_path, document)
    if condition == "source_replaced":
        with case.path.open("r+b") as source:
            source.truncate(source_bytes + 1)

    def forbidden(*args, **kwargs):
        pytest.fail("production preview cannot start a model, runtime, or resource probe")

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "auth_context", SimpleNamespace(enabled=False))
    monkeypatch.setattr(main, "local_members_service", case.members)
    monkeypatch.setattr(main, "production_repo", case.repo)
    monkeypatch.setattr(main, "artifact_store", ArtifactStore(tmp_path / "artifacts"))
    monkeypatch.setattr(main, "_creation_runtime", forbidden)
    monkeypatch.setattr(main, "_device_operation_budget", forbidden)
    monkeypatch.setattr(ModelCatalog, "status", forbidden)
    with case.repo._connect() as conn:
        token, = conn.execute("SELECT token FROM local_members WHERE member_id=?", (case.actor.member_id,)).fetchone()
    headers = {} if condition == "other_actor" else {"X-Local-Member-Token": token}
    db_before = database_snapshot(case.repo)
    archives_before = {file.relative_to(tmp_path): file.read_bytes() for file in tmp_path.rglob("*.json")}

    response = TestClient(main.app, headers=headers).get(f"/api/creation-tasks/{task.id}/execution/preview")

    assert response.status_code == 200, response.text
    data = response.json()
    assert plan.source_media["source_end_seconds"] == 6
    assert data["requirements"]["can_continue"] is True
    assert data["render_specification"]["profile"]["status"] == "verified"
    if verified:
        assert data["source_audio_input"] == {"status": "verified", "source_bytes": source_bytes, "max_input_bytes": cap}
    elif condition == "no_original_audio":
        assert "source_audio_input" not in data
    else:
        assert data["source_audio_input"] == {"status": "unknown"}
    assert database_snapshot(case.repo) == db_before
    assert {file.relative_to(tmp_path): file.read_bytes() for file in tmp_path.rglob("*.json")} == archives_before
