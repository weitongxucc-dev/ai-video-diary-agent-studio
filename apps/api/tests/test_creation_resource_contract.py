"""The creation_render resource contract must be one vocabulary end to end.

``cpu_render_facts`` persists the resource facts that ``HostResourceProbe``
later re-reads to decide the CPU render budget. These tests drive the real
producer and hand its real output to a real probe, so the contract is checked
where it is actually consumed rather than asserted in isolation.

Scope: this module proves the **resource fact contract only**. The facts are
file measurements (size, timestamps, plan fields) plus the limits the caller
configured, so the fixture uses plain non-empty isolated files and never
invokes FFmpeg. Whether a render actually decodes and produces media is
covered once, by the existing real-media execution test in
``test_creation_execution.py``, which is not duplicated here.

Verdict vocabulary of the probe is three-state: ``True`` admits, ``False``
reports an explainable over-budget rejection, and ``None`` means the facts were
not decidable, which blocks. A legal render must be decidable; an over-budget
render must say so.
"""

from __future__ import annotations
from test_host_resource_probes import render_metadata_host as render_metadata_host

import json
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from app.canvas import canvas_size
from app.creation_execution import CpuRenderLimits, cpu_render_facts
from app.host_resource_probes import (CpuRenderBudget, HostBudget, HostCapacity,
                                      HostResourceProbe, ResourceAdmissionRejected,
                                      admit_resource)
from app.montage import (CreationTaskMontagePlan, MontageClip, NarrationCue,
                         creation_task_montage_plan_to_payload)
from app.creation.narration_artifacts import NarrationArtifactRecord
from app.production import MaterialAsset, MaterialSegment

from test_creation_execution import _seed

# The exact fields the probe reads for a creation_render unit. A fact set that
# is not this shape cannot be decided, so it is part of the contract.
_RENDER_FACT_KEYS = {"kind", "execution_mode", "duration_seconds", "timeout_seconds",
                     "width_px", "height_px", "input_bytes", "clip_count",
                     "overlay_count", "max_output_bytes", "threads"}

# The registry admits a declared four-field subset for the execution-time gate.
_REGISTRY_FIELDS = {"source_duration_s", "input_count", "cpu_threads", "wall_clock_s"}

# The superseded producer shape, kept here only to prove it is no longer accepted.
_LEGACY_FACT_KEYS = {"kind", "execution_mode", "source_duration_s", "input_count",
                     "cpu_threads", "wall_clock_s", "max_output_bytes"}


def _render_budget(**overrides) -> CpuRenderBudget:
    fields = {"max_duration_seconds": 600, "max_pixel_seconds": 10 ** 12,
              "max_width_px": 4096, "max_height_px": 4096, "max_input_bytes": 10 ** 12,
              "max_clip_count": 64, "max_overlay_count": 8, "max_output_bytes": 10 ** 9,
              "max_threads": 8, "max_timeout_seconds": 600,
              "additional_memory_bytes": 1, "additional_disk_bytes": 1, "max_process_threads": 16}  # Synthetic test profile.
    fields.update(overrides)
    return CpuRenderBudget(**fields)


def _probe(tmp_path, **render_overrides) -> HostResourceProbe:
    from app.host_resource_probes import RuntimeCpuPolicy
    render_overrides.setdefault("running_cpu_policy", RuntimeCpuPolicy("darwin_nice", "synthetic-resource-metadata", nice=5))
    budget = HostBudget(100, 0, 2 ** 63, 0, 2 ** 63, 2 ** 53, 0.02,
                        cpu_render=_render_budget(**render_overrides))
    return HostResourceProbe(tmp_path / "probe.sqlite3", disk_paths=(tmp_path,), budget=budget)


def _verdict(probe: HostResourceProbe, resources: dict) -> bool | None:
    class _Request:
        pass
    request = _Request()
    request.id = "resource-contract-fixture"
    request.resources = resources
    return probe.admit(request).verdict


def test_short_preview_profile_is_independent_complete_and_has_no_guessed_defaults():
    from app.member_permits import clip_preview_budget_from_env, HostBudgetUncalibrated
    assert clip_preview_budget_from_env({}) is None
    full_movie = {'DEVICE_CPU_RENDER_' + key.upper():str(value) for key, value in asdict(_render_budget()).items()}
    assert clip_preview_budget_from_env(full_movie) is None
    with pytest.raises(HostBudgetUncalibrated, match='缺少'):
        clip_preview_budget_from_env({'DEVICE_CLIP_PREVIEW_MAX_DURATION_SECONDS':'5'})
    profile = {'DEVICE_CLIP_PREVIEW_' + key.upper():str(value) for key, value in asdict(_render_budget()).items()}
    assert clip_preview_budget_from_env(profile) == _render_budget()


def test_short_preview_admission_and_child_limits_consume_the_same_profile(tmp_path, render_metadata_host):
    from dataclasses import replace
    from app.device_scheduler import RecoveryBlocked
    profile = _render_budget(max_pixel_seconds=200)
    probe = _probe(tmp_path)
    values = {'kind':'task_clip_preview', 'execution_mode':'cpu_ffmpeg', 'duration_seconds':1.,
        'width_px':10, 'height_px':10, 'input_bytes':1, 'clip_count':1, 'overlay_count':0,
        'max_output_bytes':profile.max_output_bytes, 'threads':profile.max_threads,
        'timeout_seconds':profile.max_timeout_seconds, 'clip_preview':True, 'silent':True,
        'preview_window_limit_seconds':5., 'output_count':2}
    # An existing full-movie allowance grants no permission for the short workload.
    assert probe._sample(values).verdict is None
    probe.budget = replace(probe.budget, cpu_render=None, clip_preview=profile)
    assert probe._sample({**values, 'duration_seconds':1.1}).verdict is False  # before + after both count
    assert probe._sample({**values, 'silent':False}).verdict is False
    assert probe._sample({**values, 'output_count':1}).verdict is False
    probe._grant_resources = lambda grant: values
    limits = probe.scene_runtime_limits(object())
    assert limits.max_process_rss_bytes == profile.additional_memory_bytes
    assert limits.max_process_threads == profile.max_process_threads
    probe.budget = replace(probe.budget, clip_preview=None)
    with pytest.raises(RecoveryBlocked):
        probe.scene_runtime_limits(object())


def _render_ready_state(tmp_path, *, aspect_ratio: str = "9:16", clips: int = 1,
                        threads: int = 1, source_bytes: int = 4096):
    """A confirmed task whose render sources are plain non-empty isolated files.

    ``cpu_render_facts`` only measures these files; it never decodes them. The
    declared clip dimensions are therefore irrelevant here, which is exactly
    why the asserted output canvas comes from the aspect ratio and not from the
    input media.
    """
    repo, task_id, script_id, _digest = _seed(tmp_path)
    audio = tmp_path / "voice.wav"
    video = tmp_path / "library" / "source.mp4"
    audio.write_bytes(b"a" * 512)
    video.write_bytes(b"v" * source_bytes)
    repo._save_asset(MaterialAsset(
        id="asset_1", workspace_id="workspace_a", file_name=video.name, path=str(video),
        size_bytes=video.stat().st_size, modified_at=datetime.now(timezone.utc),
        duration_seconds=1, usage_role="b_roll", review_status="approved", analysis_status="ready",
        segments=[MaterialSegment(id="segment_1", asset_id="asset_1", start_seconds=0,
                                  end_seconds=1, description="蓝色画面", review_status="approved")],
    ))
    repo.narration_artifacts.save_narration_artifact(NarrationArtifactRecord(
        id="narration_cache_1", workspace_id="workspace_a", task_id=task_id,
        script_id=script_id, voice_profile_id="voice_1", cache_key="cache_1",
        audio_path=str(audio), duration_seconds=1, sample_rate=44100, model_id="test",
    ))
    task = repo.task_records.get_creation_task("workspace_a", task_id)
    task.narration_cache_key = "cache_1"
    task.montage_plan_id = "montage_1"
    task.stage = "montage"
    task.stage_status = "waiting_for_user"
    montage = CreationTaskMontagePlan(
        id=task.montage_plan_id, workspace_id=task.workspace_id, task_id=task_id,
        script_id=script_id, script_title="已确认文案", status="ready",
        cues=[NarrationCue("cue_1", "section_1", "真实待制作文案", 1.0)],
        clips=[MontageClip(f"clip_{index}", "cue_1", "asset_1", "segment_1", "workspace://source.mp4",
                           0, 1, 0, 1, 1, ["已确认镜头"], False) for index in range(1, clips + 1)],
        duration_seconds=1, low_confidence_clip_count=0, aspect_ratio=aspect_ratio,
        narration_audio_path="narration://artifact/cache_1",
    )
    with repo._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(task), default=str), task.workspace_id, task_id))
        conn.execute("INSERT INTO creation_task_montage_plans (id,workspace_id,task_id,payload,updated_at) "
                     "VALUES (?,?,?,?,?)",
                     (montage.id, task.workspace_id, task_id,
                      json.dumps(creation_task_montage_plan_to_payload(montage)),
                      datetime.now(timezone.utc).isoformat()))
    plan = repo.plan_records.get_creation_plan("workspace_a", "plan_workspace_a")
    return repo, task, plan, CpuRenderLimits(8_000_000, threads, 90)


def _produce(repo, task, plan, limits):
    with repo._connect() as conn:
        return cpu_render_facts(repo, conn, task, plan, limits)


# --- the produced fact set -------------------------------------------------

def test_producer_emits_exactly_the_facts_the_probe_reads(tmp_path):
    repo, task, plan, limits = _render_ready_state(tmp_path)
    _key, resources = _produce(repo, task, plan, limits)
    assert set(resources) == _RENDER_FACT_KEYS
    assert not set(resources) & _LEGACY_FACT_KEYS - {"kind", "execution_mode", "max_output_bytes"}


def test_produced_facts_match_the_real_output_canvas_and_sources(tmp_path):
    repo, task, plan, limits = _render_ready_state(tmp_path, aspect_ratio="16:9")
    _key, resources = _produce(repo, task, plan, limits)
    assert (resources["width_px"], resources["height_px"]) == canvas_size("16:9")
    assert (resources["width_px"], resources["height_px"]) != (320, 240)
    assert resources["input_bytes"] == 512 + 4096
    assert resources["clip_count"] == 1
    assert resources["overlay_count"] == 0
    assert resources["threads"] == 1
    assert resources["timeout_seconds"] == 90
    assert resources["max_output_bytes"] == 8_000_000
    assert resources["duration_seconds"] == 1


# --- the probe can decide the facts ----------------------------------------

def test_legal_facts_are_decidable_by_the_probe(tmp_path, render_metadata_host):
    repo, task, plan, limits = _render_ready_state(tmp_path)
    _key, resources = _produce(repo, task, plan, limits)
    # A finite declared host makes the facts decidable without native calibration.
    assert _verdict(_probe(tmp_path), resources) is not None


_FLOAT_BUDGET_FIELDS = {"max_duration_seconds", "max_pixel_seconds", "max_timeout_seconds"}


@pytest.mark.parametrize("fact,budget_field", [
    ("duration_seconds", "max_duration_seconds"),
    ("width_px", "max_width_px"),
    ("height_px", "max_height_px"),
    ("input_bytes", "max_input_bytes"),
    ("clip_count", "max_clip_count"),
    ("max_output_bytes", "max_output_bytes"),
    ("threads", "max_threads"),
    ("timeout_seconds", "max_timeout_seconds"),
])
def test_over_budget_facts_are_reported_as_over_budget_not_as_unknown(tmp_path, fact, budget_field, render_metadata_host):
    # Two clips and two threads so every field admits a legal budget strictly
    # below the produced fact. A zero budget is itself not a legal budget, so
    # the float ceilings are halved rather than decremented.
    repo, task, plan, limits = _render_ready_state(tmp_path, clips=2, threads=2)
    _key, resources = _produce(repo, task, plan, limits)
    ceiling = resources[fact]
    probe = _probe(tmp_path, **{budget_field: ceiling / 2 if budget_field in _FLOAT_BUDGET_FIELDS else ceiling - 1})
    assert _verdict(probe, resources) is False


def test_pixel_seconds_budget_is_enforced(tmp_path, render_metadata_host):
    repo, task, plan, limits = _render_ready_state(tmp_path)
    _key, resources = _produce(repo, task, plan, limits)
    pixels = resources["width_px"] * resources["height_px"] * resources["duration_seconds"]
    assert _verdict(_probe(tmp_path, max_pixel_seconds=pixels - 1), resources) is False


@pytest.mark.parametrize("missing", sorted(_RENDER_FACT_KEYS - {"kind", "execution_mode"}))
def test_any_missing_fact_blocks_instead_of_being_guessed(tmp_path, missing, render_metadata_host):
    repo, task, plan, limits = _render_ready_state(tmp_path)
    _key, resources = _produce(repo, task, plan, limits)
    resources.pop(missing)
    assert _verdict(_probe(tmp_path), resources) is None


# --- the execution-time registry subset stays in sync ----------------------

def test_executor_registry_subset_is_built_from_the_published_fact_names(tmp_path):
    """The executor admits a built four-field subset, not the probe fact set.

    This mirrors the mapping the executor performs on the persisted request
    before ``admit_resource``. The superseded producer shape is fed as a real
    payload to show it can no longer reach the gate unnoticed.
    """
    repo, task, plan, limits = _render_ready_state(tmp_path)
    _key, resources = _produce(repo, task, plan, limits)
    subset = {
        "source_duration_s": float(resources["duration_seconds"]),
        "input_count": int(resources["clip_count"]),
        "cpu_threads": int(resources["threads"]),
        "wall_clock_s": float(resources["timeout_seconds"]),
    }
    assert set(subset) == _REGISTRY_FIELDS
    # The gate itself stays real; only the ambient host sample is pinned so a busy
    # machine cannot decide whether this contract check passes.
    capacity = HostCapacity(cpu_count=8, cpu_threads_ceiling=7,
                            available_memory_bytes=8 * 1024 ** 3, load_avg_1m=0.5)
    assert admit_resource("creation_render", subset, capacity=capacity) is not None
    legacy = {name: 1 for name in _LEGACY_FACT_KEYS}
    with pytest.raises(ResourceAdmissionRejected, match="undeclared field"):
        admit_resource("creation_render", legacy, capacity=capacity)


# --- input changes reject a stale request ---------------------------------

def test_aspect_ratio_change_rejects_the_previous_request(tmp_path):
    repo, task, plan, limits = _render_ready_state(tmp_path, aspect_ratio="9:16")
    first_key, first_resources = _produce(repo, task, plan, limits)
    with repo._connect() as conn:
        row = conn.execute(
            "SELECT payload FROM creation_task_montage_plans WHERE workspace_id=? AND task_id=? AND id=?",
            (task.workspace_id, task.id, task.montage_plan_id)).fetchone()
    payload = json.loads(row["payload"])
    payload["aspect_ratio"] = "16:9"
    with repo._connect() as conn:
        conn.execute(
            "UPDATE creation_task_montage_plans SET payload=? WHERE workspace_id=? AND task_id=? AND id=?",
            (json.dumps(payload, default=str), task.workspace_id, task.id, task.montage_plan_id))
    second_key, second_resources = _produce(repo, task, plan, limits)
    assert second_resources["width_px"] == canvas_size("16:9")[0]
    assert first_key != second_key
    assert first_resources != second_resources


def test_clip_input_change_rejects_the_previous_request(tmp_path):
    repo, task, plan, limits = _render_ready_state(tmp_path)
    first_key, first_resources = _produce(repo, task, plan, limits)
    source = repo.resolve_clip_media_reference("workspace_a", "asset_1", "workspace://source.mp4")
    with open(source, "ab") as handle:
        handle.write(b"v" * 64)
    second_key, second_resources = _produce(repo, task, plan, limits)
    assert second_resources["input_bytes"] == first_resources["input_bytes"] + 64
    assert first_key != second_key
    assert first_resources != second_resources


@pytest.mark.parametrize("boundary,reason", [("missing_policy", "cpu_policy_unavailable"),
    ("non_darwin", "cpu_policy_platform_unsupported")])
def test_declared_policy_keeps_unavailable_platform_boundaries(tmp_path, monkeypatch, render_metadata_host, boundary, reason):
    from types import SimpleNamespace
    import app.host_resource_probes as probes
    repo, task, plan, limits = _render_ready_state(tmp_path)
    _, resources = _produce(repo, task, plan, limits)
    probe = _probe(tmp_path, **({'running_cpu_policy': None} if boundary == 'missing_policy' else {}))
    if boundary == 'non_darwin':
        monkeypatch.setattr(probes, 'sys', SimpleNamespace(platform='linux'))
    decision = probe._sample(resources)
    assert decision.verdict is None and decision.reason == reason
