"""The production preview verifies saved canvas limits without running media."""
from dataclasses import asdict, replace
import hashlib
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app import main
from app.artifact_store import ArtifactStore
from app.creation.render_specification import render_target
from app.device_resource_configuration import backend_identity, current_device_identity
from app.model_runtime import ModelCatalog
from test_device_resource_profile import profile_document, write_profile
from test_source_execution_requirements import database_snapshot, prepared_source
from test_source_task_use_case import source_case as source_case


def write_render_profile(root, settings, *, width, height, recorded=True):
    document = profile_document()
    document["device"] = asdict(current_device_identity({"DEVICE_ID": "render-spec-fixture"}))
    budget = document["budget"]["cpu_render"]
    budget.update(max_width_px=width, max_height_px=height)
    identity = backend_identity("cpu_render", None, settings=settings)
    assert identity
    document["backends"]["cpu_render"] = identity
    if recorded:
        receipt = {"format_version": 1, "device": document["device"], "backend_identity": identity,
            "budget_digest": hashlib.sha256(json.dumps(budget, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "method": "synthetic profile binding test", "input_range": {"width_px": width, "height_px": height},
            "observations": {"process_peak_rss_bytes": 100}}
        raw = json.dumps(receipt).encode()
        receipts = root / "resource-profile-evidence"
        receipts.mkdir(exist_ok=True)
        (receipts / "render.json").write_bytes(raw)
        document["evidence"] = {"cpu_render": {"record_id": "render", "sha256": hashlib.sha256(raw).hexdigest()}}
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
        size, operation = render_target(source_case.repo, task, replace(plan, aspect_ratio="9:16"), connection=connection)
        assert size == (1920, 1080) and operation == "cpu_render"
        size, operation = render_target(source_case.repo, task, replace(plan, presenter_share_target=30), connection=connection)
        assert size == (1920, 1080) and operation is None
