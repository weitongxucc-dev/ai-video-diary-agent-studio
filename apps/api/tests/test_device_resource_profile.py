"""Profile selection is not calibration, live admission or a workspace setting."""
from dataclasses import asdict
import hashlib
import json

import pytest

from app.device_resource_profile import (
    DeviceProfileIdentity, DeviceProfileUnavailable, PROFILE_FILENAME,
    load_device_resource_profile, resolve_experimental_budget,
)


DEVICE = DeviceProfileIdentity("fixture-device", "darwin", "arm64", 16 * 1024**3)
BACKENDS = {"cpu_render": "ffmpeg-fixture:render-contract-v21", "narration": "qwen-fixture-v1"}


def profile_document():
    # Synthetic finite numbers exercise the existing contracts; no production claim.
    return {
        "format_version": 1, "profile_id": "fixture-profile", "revision": 3,
        "device": asdict(DEVICE), "backends": dict(BACKENDS),
        "budget": {
            "max_cpu_percent": 60, "min_available_memory_bytes": 1024**3,
            "min_free_disk_bytes": 2 * 1024**3, "max_disk_io_bytes_per_second": 20 * 1024**2,
            "max_unit_input_bytes": 256 * 1024**2,
            "memory_policy": {"platform": "darwin", "recovery_stable_seconds": 6,
                "max_sample_gap_seconds": 3, "evidence_reference": "unverified-text"},
            "cpu_render": {"max_duration_seconds": 30, "max_pixel_seconds": 1920 * 1080 * 30,
                "max_width_px": 1920, "max_height_px": 1080, "max_input_bytes": 256 * 1024**2,
                "max_clip_count": 6, "max_overlay_count": 2, "max_output_bytes": 32 * 1024**2,
                "max_threads": 2, "max_timeout_seconds": 300, "additional_memory_bytes": 1024**3,
                "additional_disk_bytes": 3 * 1024**3, "max_process_threads": 16,
                "running_cpu_policy": {"mode": "darwin_nice", "nice": 10,
                    "evidence_reference": "also-unverified-text"}},
            "narration": {"max_text_chars": 100, "max_section_count": 1,
                "max_reference_bytes": 1024, "max_duration_seconds": 30,
                "max_output_bytes": 3 * 1024**2, "max_threads": 1, "max_timeout_seconds": 300,
                "max_process_rss_bytes": 256 * 1024**2, "max_process_threads": 16,
                "runtime_sample_seconds": .1, "additional_memory_bytes": 256 * 1024**2,
                "additional_disk_bytes": 32 * 1024**2, "input_scope": "single_call_experiment_v1"},
        },
    }


def write_profile(tmp_path, document):
    path = tmp_path / PROFILE_FILENAME
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def load(path, *, backends=BACKENDS, device=DEVICE):
    return load_device_resource_profile(path, device=device, backend_identities=backends)


def test_normal_profile_does_not_fall_back_to_inherited_experiment_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICE_HOST_MAX_CPU_PERCENT", "99")
    monkeypatch.setenv("DEVICE_CPU_RENDER_MAX_OUTPUT_BYTES", "9999999999")
    with pytest.raises(DeviceProfileUnavailable, match="FileNotFoundError"):
        load(tmp_path / PROFILE_FILENAME)
    resolved = load(write_profile(tmp_path, profile_document()))
    assert resolved.budget.max_cpu_percent == 60
    assert resolved.budget.cpu_render.max_output_bytes == 32 * 1024**2
    assert resolved.source_kind == "device_profile"
    assert resolved.revision == 3


def test_nested_contracts_are_real_budgets_but_evidence_text_is_not_verification(tmp_path):
    resolved = load(write_profile(tmp_path, profile_document()))
    assert resolved.budget.memory_policy.platform == "darwin"
    assert resolved.budget.cpu_render.running_cpu_policy.mode == "darwin_nice"
    assert resolved.budget.narration.input_scope == "single_call_experiment_v1"
    assert resolved.budget.presenter is None
    assert resolved.budget.max_swap_used_bytes is None
    assert resolved.evidence == {name: {"status": "declared", "verification_status": "unknown"}
                                 for name in ("host", "cpu_render", "narration")}


def test_model_change_disables_only_affected_operation(tmp_path):
    resolved = load(write_profile(tmp_path, profile_document()),
                    backends={**BACKENDS, "narration": "qwen-fixture-v2"})
    assert resolved.budget.narration is None
    assert resolved.budget.cpu_render is not None
    assert resolved.unavailable == {"narration": "backend_identity_changed_or_unavailable"}
    assert "narration" not in resolved.evidence


def test_profile_does_not_transfer_to_another_device_or_memory_configuration(tmp_path):
    path = write_profile(tmp_path, profile_document())
    with pytest.raises(DeviceProfileUnavailable, match="different device"):
        load(path, device=DeviceProfileIdentity("fixture-device", "darwin", "arm64", 8 * 1024**3))


def test_measurement_receipt_binds_device_backend_and_exact_budget(tmp_path):
    document = profile_document()
    payload = json.dumps(document["budget"]["cpu_render"], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode()
    receipt = {"format_version": 1, "device": asdict(DEVICE),
        "backend_identity": BACKENDS["cpu_render"], "budget_digest": hashlib.sha256(payload).hexdigest(),
        "method": "synthetic fixture only", "input_range": {"width_px": 1920},
        "observations": {"process_peak_rss_bytes": 100}}
    receipt_raw = json.dumps(receipt).encode()
    receipts = tmp_path / "resource-profile-evidence"
    receipts.mkdir()
    (receipts / "sample-1.json").write_bytes(receipt_raw)
    document["evidence"] = {"cpu_render": {"record_id": "sample-1",
        "sha256": hashlib.sha256(receipt_raw).hexdigest()}}
    path = write_profile(tmp_path, document)
    resolved = load(path)
    assert resolved.evidence["cpu_render"]["status"] == "recorded"
    assert resolved.evidence["cpu_render"]["verification_status"] == "unknown"
    document["budget"]["cpu_render"]["max_clip_count"] = 20
    write_profile(tmp_path, document)
    with pytest.raises(DeviceProfileUnavailable, match="does not bind"):
        load(path)


def test_profile_rejects_undeclared_fields_and_forged_receipt_path(tmp_path):
    document = profile_document()
    document["budget"]["current_available_memory_bytes"] = 10 * 1024**3
    with pytest.raises(DeviceProfileUnavailable, match="unsupported fields"):
        load(write_profile(tmp_path, document))
    document = profile_document()
    document["evidence"] = {"cpu_render": {"record_id": "../../foreign", "sha256": "0" * 64}}
    with pytest.raises(DeviceProfileUnavailable, match="reference is invalid"):
        load(write_profile(tmp_path, document))


def test_existing_budget_validation_is_preserved_and_unknown_fields_do_not_disappear(tmp_path):
    document = profile_document()
    document["budget"]["cpu_render"]["max_process_threads"] = 1
    with pytest.raises(DeviceProfileUnavailable, match="thread cap"):
        load(write_profile(tmp_path, document))
    document = profile_document()
    document["budget"]["narration"]["max_output_bytes"] = True
    with pytest.raises(DeviceProfileUnavailable, match="invalid type"):
        load(write_profile(tmp_path, document))
    document = profile_document()
    document["budget"]["min_free_disk_bytes"] = 1.5
    with pytest.raises(DeviceProfileUnavailable, match="invalid type"):
        load(write_profile(tmp_path, document))


def test_experiment_is_explicit_and_never_reported_as_verified(tmp_path):
    normal = load(write_profile(tmp_path, profile_document()))
    experiment = resolve_experimental_budget(normal.budget, experiment_id="controlled-load-fixture")
    assert experiment.budget is normal.budget
    assert experiment.source_kind == "explicit_experiment"
    assert experiment.revision is None
    assert all(value["verification_status"] == "unknown" for value in experiment.evidence.values())
