"""Presenter preparation owns one device receipt and a bounded measured window."""

from dataclasses import asdict
import hashlib
import json
import subprocess
import wave
from types import SimpleNamespace

import pytest

from app.avatar_lipsync import LatentSync16Provider
from app.device_resource_preparation import request_kind
from app.device_resource_profile import DeviceProfileUnavailable
from app.device_runtime import build_device_runtime
from app.granted_subprocess import GrantedCommandResult, ObservedCommandResources
from app.lipsync_models import LATENTSYNC_16_MODEL_ID, LatentSync16Runtime
from app.presenter_resource_preparation import PresenterPreparationShape, PresenterResourcePreparation
from app.schemas import DevicePreparationRequest
from test_device_resource_preparation import preparation
from test_device_resource_profile import BACKENDS, profile_document
from test_device_scheduler import Processes, Resources


def preparation_fixture(tmp_path, monkeypatch):
    repository, actor, access, cpu, paths = preparation(tmp_path)
    document = profile_document()
    document["device"] = asdict(cpu.device)
    cpu.profile_path.parent.mkdir(parents=True, exist_ok=True)
    cpu.profile_path.write_text(json.dumps(document))
    runtime = LatentSync16Runtime(tmp_path, tmp_path / "runtime-python", tmp_path / "worker.py", "mlx")
    catalog = SimpleNamespace(latentsync_runtime=runtime)
    provider = LatentSync16Provider(catalog, None, temporary_root=paths["temporary"])
    fixture_folder = tmp_path / "calibration"
    fixture_folder.mkdir()
    hashes = {}
    (fixture_folder / "visual.mp4").write_bytes(b"visual fixture")
    with wave.open(str(fixture_folder / "driver.wav"), "wb") as wav:
        wav.setparams((1, 2, 16000, 10240, "NONE", "not compressed"))
        wav.writeframes(b"\x04\x00" * 10240)
    for name in ("visual.mp4", "driver.wav"):
        hashes[name] = hashlib.sha256((fixture_folder / name).read_bytes()).hexdigest()
    import app.presenter_resource_preparation as presenter

    presenter_identity = ["mlx-test-v1"]
    monkeypatch.setattr(presenter, "CALIBRATION_FILES", hashes)
    monkeypatch.setattr(presenter, "backend_identity", lambda name, *args, **kwargs:
                        presenter_identity[0] if name == "presenter" else BACKENDS.get(name))
    monkeypatch.setattr(presenter, "sample_host_memory",
                        lambda: SimpleNamespace(pressure="normal", available_bytes=8 * 1024**3))
    service = PresenterResourcePreparation(model_catalog=catalog, provider=provider,
        profile_path=cpu.profile_path, settings=cpu.settings, device=cpu.device, write_paths=paths,
        verify_identity=access.verify_identity)
    return repository, actor, access, service, presenter_identity


def test_presenter_device_preparation_shape_is_explicit():
    from uuid import uuid4
    request = {"request_id": str(uuid4()), "kind": "presenter", "shape": {}}
    parsed = DevicePreparationRequest.model_validate(request)
    assert PresenterPreparationShape(**parsed.shape.model_dump()).duration_seconds == 0.64
    multiple = DevicePreparationRequest.model_validate({**request, "shape": {"duration_seconds": 7.9}})
    assert PresenterPreparationShape(**multiple.shape.model_dump()).windows == 13
    with pytest.raises(ValueError):
        DevicePreparationRequest.model_validate({**request, "shape": {"duration_seconds": 120}})
    with pytest.raises(ValueError):
        DevicePreparationRequest.model_validate({**request, "shape": {"visual_path": "/personal/clip.mp4"}})


@pytest.mark.parametrize("duration", [0.64, 1.333])
def test_presenter_calibration_binds_the_measured_invocation(tmp_path, monkeypatch, duration):
    repository, actor, access, service, identity = preparation_fixture(tmp_path, monkeypatch)
    profile_before = json.loads(service.profile_path.read_text())
    shape = PresenterPreparationShape(duration_seconds=duration)
    plan_id, budget = service.prepare(shape, actor, request_id="presenter-test")
    assert budget.presenter.preparation_scope == "windowed_probe_v1"
    assert json.loads(service.profile_path.read_text()) == profile_before

    def render(*, request, output_path, grant_context):
        assert request.model_id == LATENTSYNC_16_MODEL_ID
        assert request.visual_path.endswith("/calibration/visual.mp4")
        with wave.open(request.audio_path, "rb") as driver:
            assert driver.getnframes() == shape.samples
        grant_context.run([str(service.provider.runtime_path), str(service.provider.worker_path)])
        output_path.write_bytes(b"synthetic test only")
        return SimpleNamespace(status="succeeded", artifact_path=str(output_path), alignment_evidence=json.dumps(
            {"backend": "apple_mlx", "engine": LATENTSYNC_16_MODEL_ID, "rendered_frames": shape.frames,
             "audio_conditioning_samples": shape.samples, "width": 1080, "height": 1920, "window_count": shape.windows,
             "inference_resolution": 512, "scratch_file_bytes": 140, "peak_mlx_allocation_bytes": 2 * 1024**2}))

    monkeypatch.setattr(service.provider, "render", render)
    def run(_scheduler, _grant, command, **kwargs):
        if "-af" in command:
            subprocess.run(command, check=True)
        return GrantedCommandResult(0, b"", b"", ObservedCommandResources(1024**2, 3, True, True))

    monkeypatch.setattr("app.granted_subprocess.run_granted_command", run)
    runtime = build_device_runtime(database_path=repository.database_path, device_lock_path=tmp_path / "lock",
        device_access=access, registrations={"device_preparation": service.registration()},
        resources=Resources(), processes=Processes())
    try:
        scheduler = runtime.scheduler
        submitted = scheduler.submit(service.intent(plan_id, actor), actor)
        assert request_kind(database_path=repository.database_path, request_id=submitted.id, actor=actor) == "presenter"
        with pytest.raises(DeviceProfileUnavailable, match="完成回执"):
            service.finalize(database_path=repository.database_path, request_id=submitted.id, actor=actor)
        service.execute(scheduler.pick_and_grant(), scheduler)
        outcome = service.status(database_path=repository.database_path, request_id=submitted.id, actor=actor)
        assert outcome["state"] == "done"
        assert outcome["measurement"]["peak_process_group_threads"] == 3
        assert outcome["measurement"]["scratch_file_bytes"] == 140
        profile = service.finalize(database_path=repository.database_path, request_id=submitted.id, actor=actor)
        assert profile.budget.presenter.preparation_scope == "windowed_measured_v1"
        assert profile.budget.presenter.max_duration_seconds == duration
        assert outcome["measurement"]["windows"] == shape.windows
        assert profile.budget.presenter.max_source_width_px == 1080
        assert profile.budget.presenter.max_source_height_px == 1920
        assert profile.budget.presenter.additional_memory_bytes > profile.budget.presenter.max_process_rss_bytes
        assert json.loads(service.profile_path.read_text())["budget"]["narration"] == profile_before["budget"]["narration"]
        assert service.finalize(database_path=repository.database_path, request_id=submitted.id, actor=actor).digest == profile.digest
        identity[0] = "mlx-test-v2"
        assert service._load().budget.presenter is None
        assert service._load().budget.narration is not None
    finally:
        assert runtime.stop(2)


def test_presenter_fixture_changes_cannot_be_admitted(tmp_path, monkeypatch):
    _, actor, _, service, _ = preparation_fixture(tmp_path, monkeypatch)
    original = service.profile_path.read_bytes()
    (tmp_path / "calibration" / "visual.mp4").write_bytes(b"other.mp4!")
    with pytest.raises(DeviceProfileUnavailable, match="校验"):
        service.prepare(PresenterPreparationShape(), actor, request_id="changed-fixture")
    assert service.profile_path.read_bytes() == original


def test_presenter_setup_keeps_profile_when_weight_residency_cannot_fit(tmp_path, monkeypatch):
    _, actor, _, service, _ = preparation_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr("app.presenter_resource_preparation.sample_host_memory",
                        lambda: SimpleNamespace(pressure="warning", available_bytes=2 * 1024**3))
    before = service.profile_path.read_bytes()
    with pytest.raises(DeviceProfileUnavailable, match="低于 LatentSync 1.6"):
        service.prepare(PresenterPreparationShape(), actor, request_id="insufficient")
    assert service.profile_path.read_bytes() == before
    assert not (service.root / "plans").exists() or not list((service.root / "plans").glob("*.json"))
