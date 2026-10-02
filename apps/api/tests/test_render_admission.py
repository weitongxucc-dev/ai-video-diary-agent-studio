"""Resource admission wiring for the CPU render path (WIL-65).

The montage renderer now admits every licensed render through the SCHED
``creation_render`` registry before spawning any subprocess. These tests pin the
wiring: legal requests admit deterministically, over-limit / type-mismatch
requests are rejected with a readable reason, sampling failure is fail-closed,
and the render path refuses to spawn ffmpeg when admission fails.
"""

from __future__ import annotations

import time

import psutil
import pytest

from app.host_resource_probes import (
    HostCapacity,
    ResourceAdmissionRejected,
    ResourceSamplingUnavailable,
)
from app.montage import (
    MontageClip,
    MontagePlan,
    MontageRenderer,
    NarrationCue,
    admit_creation_render,
)


def _capacity(*, ceiling=8, cpu_count=16, memory=2**33, load=0.5):
    return HostCapacity(cpu_count=cpu_count, cpu_threads_ceiling=ceiling,
                        available_memory_bytes=memory, load_avg_1m=load)


def test_admit_creation_render_legal_returns_deterministic_budget():
    capacity = _capacity()
    request = dict(source_duration_s=12.5, input_count=3, cpu_threads=4, wall_clock_s=120.0)
    first = admit_creation_render(**request, capacity=capacity)
    second = admit_creation_render(**request, capacity=capacity)
    assert first == second
    assert first.kind == "creation_render"
    assert first.granted == request
    assert first.cpu_threads_ceiling == capacity.cpu_threads_ceiling


def test_admit_creation_render_rejects_over_limit():
    # Static registry cap.
    with pytest.raises(ResourceAdmissionRejected, match="source_duration_s"):
        admit_creation_render(source_duration_s=3601.0, input_count=1,
                              cpu_threads=1, wall_clock_s=60.0, capacity=_capacity())
    # Sampled host ceiling below the static cap still rejects conservatively.
    with pytest.raises(ResourceAdmissionRejected, match="sampled host ceiling"):
        admit_creation_render(source_duration_s=1.0, input_count=1,
                              cpu_threads=4, wall_clock_s=60.0, capacity=_capacity(ceiling=2))


def test_admit_creation_render_rejects_type_mismatch():
    with pytest.raises(ResourceAdmissionRejected, match="integer"):
        admit_creation_render(source_duration_s=1.0, input_count=1.5,
                              cpu_threads=1, wall_clock_s=60.0, capacity=_capacity())
    with pytest.raises(ResourceAdmissionRejected, match="finite number"):
        admit_creation_render(source_duration_s="1.0", input_count=1,
                              cpu_threads=1, wall_clock_s=60.0, capacity=_capacity())


def test_admit_creation_render_sampling_unavailable_fails_explicitly(monkeypatch):
    monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: None)
    with pytest.raises(ResourceSamplingUnavailable):
        admit_creation_render(source_duration_s=1.0, input_count=1,
                              cpu_threads=1, wall_clock_s=60.0)


def _over_limit_plan(tmp_path):
    return MontagePlan(
        id="over-limit", workspace_id="workspace_a", batch_id="batch", episode_id="ep",
        script_title="超限", status="ready",
        cues=[NarrationCue("cue", "section", "超限", 4000.0)],
        clips=[MontageClip("clip", "cue", "asset", "segment", str(tmp_path / "missing.mp4"),
                           0.0, 4000.0, 0.0, 4000.0, 1.0, ["test"], False)],
        duration_seconds=4000.0, low_confidence_clip_count=0,
    )


def test_render_path_rejects_over_limit_before_spawning_subprocess(tmp_path):
    plan = _over_limit_plan(tmp_path)

    class StubScheduler:
        pass

    renderer = MontageRenderer(tmp_path / "output")
    with pytest.raises(ResourceAdmissionRejected, match="source_duration_s"):
        renderer.render(plan, tmp_path / "voice.wav", scheduler=StubScheduler(),
                        grant="grant", max_output_bytes=1_000_000, threads=1,
                        deadline_monotonic=time.monotonic() + 60)
    assert plan.status == "failed"
    assert "资源准入" in plan.message
