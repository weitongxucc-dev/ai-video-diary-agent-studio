"""Read-only facts about installed execution units, not task admission."""
from __future__ import annotations

from collections.abc import Mapping


# Only numeric limits are public; budgets also contain private tool paths and hashes.
_LIMIT_UNITS = {
    "max_text_chars": "characters", "max_section_count": "sections",
    "max_asset_count": "assets", "max_input_count": "inputs",
    "max_clip_count": "clips", "max_overlay_count": "overlays",
    "max_reference_bytes": "bytes", "max_input_bytes": "bytes",
    "max_output_bytes": "bytes", "max_pcm_bytes": "bytes",
    "max_frame_json_bytes": "bytes", "max_report_bytes": "bytes",
    "max_stderr_bytes": "bytes", "max_duration_seconds": "seconds",
    "max_source_duration_seconds": "seconds", "max_output_duration_seconds": "seconds",
    "max_timeout_seconds": "seconds", "max_sample_rate": "hertz",
    "max_channels": "channels", "max_threads": "threads", "decoder_threads": "threads",
    "max_process_threads": "threads", "max_process_rss_bytes": "bytes",
    "max_width_px": "pixels", "max_height_px": "pixels", "max_pixel_seconds": "pixel_seconds",
    "additional_memory_bytes": "bytes", "additional_disk_bytes": "bytes",
    "runtime_sample_seconds": "seconds",
}
_SAFETY_FIELDS = {
    "max_process_threads", "max_process_rss_bytes", "additional_memory_bytes",
    "additional_disk_bytes", "runtime_sample_seconds",
}


def _methods(target, *names):
    return target is not None and all(callable(getattr(target, name, None)) for name in names)


def _limits(budget, *, narration=False, purpose=None):
    if budget is None:
        return []
    limits = []
    for name, unit in _LIMIT_UNITS.items():
        value = getattr(budget, name, None)
        if type(value) not in (int, float):
            continue
        scope = "safety" if name in _SAFETY_FIELDS else "unit"
        if narration:
            if name in {"max_timeout_seconds", "max_output_bytes"}:
                scope = "legacy_parent_ceiling"
            elif name in {"max_text_chars", "max_section_count", "max_duration_seconds"}:
                scope = ("unit" if budget.input_scope == "single_call_experiment_v1"
                         else "legacy_stage")
        limit = {"name": name, "value": value, "unit": unit, "scope": scope}
        if purpose == "pcm_append":
            if name == "max_output_bytes":
                limit.update(scope="artifact_total", subject="assembled_wav_file",
                             accounting="absolute_size_including_existing_pcm_and_wav_header")
            elif name == "max_input_count":
                limit.update(subject="append_inputs", accounting="new_sources_plus_existing_prefix",
                             existing_prefix_slots=1)
            elif name == "max_input_bytes":
                limit.update(subject="append_input_files",
                             accounting="new_source_files_plus_existing_pcm_and_prefix_wav_header")
        limits.append(limit)
    return limits


def _capability(purpose, mode, budget, installed, *, continuation, gaps, narration=False):
    return {
        "purpose": purpose,
        "execution_mode": mode,
        "implementation_status": "installed" if installed else "not_installed",
        "implementation_basis": "registered_owner_executor_interfaces",
        "configured": budget is not None,
        "provenance": "configured" if budget is not None else "unconfigured",
        "verification_status": "unknown",
        "evidence_ref": None,
        "verified_input_range": None,
        "limits": _limits(budget, narration=narration, purpose=purpose),
        "continuation": continuation if installed else [],
        "gaps": gaps,
    }


def execution_capability_facts(*, budget, registrations: Mapping) -> dict:
    """Project the current runtime's budgets and existing owner registrations.

    The caller authorizes the task. No environment, repository, resource probe or
    owner method is executed. Configuration has no trusted measurement binding.
    """
    creation = registrations.get("creation_job")
    owner = getattr(creation, "owner", None)
    executor = getattr(creation, "executor", None)
    segments = getattr(owner, "segments", None)
    quality = registrations.get("quality_check")
    quality_owner = getattr(quality, "owner", None)
    quality_executor = getattr(quality, "executor", None)
    narration = getattr(budget, "narration", None)
    audio = getattr(budget, "cpu_audio", None)
    render = getattr(budget, "cpu_render", None)
    montage = getattr(budget, "montage", None)
    inspection = getattr(budget, "quality", None)
    creation_installed = _methods(owner, "verify_submission") and _methods(executor, "execute")
    segment_installed = (creation_installed and _methods(segments, "next_initial")
                         and getattr(executor, "narration_stage", None) is not None)
    pcm_installed = (creation_installed and _methods(owner, "pcm_assembly_baseline")
                     and _methods(executor, "recover_pcm_unit"))
    picture_installed = (creation_installed and _methods(owner, "render_facts")
                         and _methods(executor, "render_action"))
    mix_installed = picture_installed and _methods(owner, "audio_mix_facts")
    quality_installed = (_methods(quality_owner, "verify_submission")
                         and _methods(quality_executor, "execute"))
    return {
        "admission": "not_evaluated",
        "whole_task_support": "not_inferred",
        "task_total_budget": {
            "provenance": "not_supplied",
            "verification_status": "unknown",
            "limits": [],
            "gaps": ["Read the task's persisted authorization and cumulative usage; runtime ceilings do not establish its total quota."],
        },
        "capabilities": [
            _capability("narration_segment", "model_worker", narration, segment_installed,
                        continuation=["persisted_segment_checkpoint"], narration=True,
                        gaps=["Input scope and legacy aggregate limits do not establish arbitrary task scale."]),
            _capability("pcm_append", "cpu_pcm_wav_assembly", audio, pcm_installed,
                        continuation=["persisted_append_receipt", "recover_original_unit"],
                        gaps=["Fixed PCM append support does not establish final mixed-track capacity.",
                              "A new grant does not reset the cumulative WAV cap or prefix accounting; per-request max_new_bytes must fit remaining capacity."]),
            _capability("final_audio_mix", "cpu_ffmpeg", audio, mix_installed,
                        continuation=[],
                        gaps=["Final mix is one bounded invocation; persistent cross-unit mixing is not established."]),
            _capability("storyboard", "metadata_only", montage,
                        creation_installed and getattr(executor, "montage_stage", None) is not None,
                        continuation=[], gaps=["One storyboard declaration is bounded by its input limits."]),
            _capability("picture_render", "cpu_ffmpeg", render, picture_installed,
                        continuation=["serial_work_inside_one_grant"],
                        gaps=["Serial clip processing is not persistent cross-unit final composition."]),
            _capability("quality_check", "isolated_quality_python", inspection, quality_installed,
                        continuation=[],
                        gaps=["Whole-file quality inspection is bounded; range-based aggregation is not established."]),
        ],
    }
