import json
from dataclasses import asdict

import pytest

from app.host_resource_probes import HostBudget, HostResourceProbe, ObservationBudget
from app.member_permits import HostBudgetUncalibrated, observation_budget_from_env


def budget():
    # Finite test envelope only; never installed as a production default.
    return ObservationBudget("a" * 64, 3, 1280, 1280, 1024, 1920 * 1080, 4096,
                             1, 30, 512 * 1024 * 1024, 12, .1, 512 * 1024 * 1024, 4096)


def test_ocr_has_no_default_and_requires_exact_measured_fields():
    assert observation_budget_from_env({}) is None
    data = asdict(budget())
    assert observation_budget_from_env({"TASK_OUTPUT_OBSERVATION_BUDGET_JSON": json.dumps(data)}) == budget()
    del data["max_source_pixels"]
    with pytest.raises(HostBudgetUncalibrated, match="全部显式"):
        observation_budget_from_env({"TASK_OUTPUT_OBSERVATION_BUDGET_JSON": json.dumps(data)})


def test_worker_digest_and_source_pixels_are_enforced_before_host_work(tmp_path):
    probe = HostResourceProbe(tmp_path / "test.sqlite", disk_paths=(tmp_path,),
        budget=HostBudget(100, 1, 2**63, 1, 2**63, 1024, observation=budget()))
    resources = {"kind": "task_output_observation", "execution_mode": "local_ffmpeg_apple_vision",
        "worker_sha256": "b" * 64, "sample_count": 3, "width_px": 1280, "height_px": 720,
        "source_pixels": 1920 * 1080, "input_bytes": 100, "max_output_bytes": 4096,
        "threads": 1, "timeout_seconds": 30}
    assert probe._sample(resources).reason == "observation_worker_not_calibrated"
    resources.update(worker_sha256="a" * 64, source_pixels=1920 * 1080 + 1)
    assert probe._sample(resources).reason == "declared_resources_exceed_budget"
    probe._grant_resources = lambda _: {**resources, "source_pixels": 1920 * 1080}
    limits = probe.scene_runtime_limits(None)
    assert limits.max_process_rss_bytes == budget().max_process_rss_bytes
    assert limits.max_process_threads == 12
