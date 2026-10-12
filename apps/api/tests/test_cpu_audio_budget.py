from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from app.host_resource_probes import CpuAudioBudget, HostBudget, HostResourceProbe
from app.member_permits import cpu_audio_budget_from_env, HostBudgetUncalibrated


def audio_budget():
    return CpuAudioBudget(max_input_count=8, max_input_bytes=100000, max_source_duration_seconds=120,
        max_output_duration_seconds=120, max_sample_rate=48000, max_channels=8, max_output_bytes=1000000,
        max_threads=1, max_timeout_seconds=30, max_process_rss_bytes=1000000, max_process_threads=8,
        runtime_sample_seconds=.1, additional_memory_bytes=2000000, additional_disk_bytes=2000000)


def audio_resources():
    return {'kind': 'task_audio', 'execution_mode': 'cpu_ffmpeg', 'input_count': 1, 'input_bytes': 1000,
        'source_duration_limit_seconds': 1, 'output_duration_limit_seconds': 1,
        'source_sample_rate_limit': 48000, 'source_channels_limit': 8, 'output_sample_rate_limit':48000, 'output_channels_limit':2,
        'input_policy':'probe_then_bounded_decode', 'inputs':[{'path':'/isolated.wav'}],
        'max_output_bytes': 100000, 'threads': 1, 'timeout_seconds': 5}


def test_audio_environment_requires_its_own_complete_calibration():
    assert cpu_audio_budget_from_env({}) is None
    with pytest.raises(HostBudgetUncalibrated):
        cpu_audio_budget_from_env({'DEVICE_CPU_AUDIO_MAX_THREADS': '1'})
    values = {'DEVICE_CPU_AUDIO_'+name.upper(): str(value) for name, value in asdict(audio_budget()).items()}
    assert cpu_audio_budget_from_env(values) == audio_budget()
    values['DEVICE_CPU_AUDIO_MAX_THREADS'] = '0'
    with pytest.raises(HostBudgetUncalibrated):
        cpu_audio_budget_from_env(values)


@pytest.mark.parametrize('field', ['input_count', 'input_bytes', 'source_duration_limit_seconds', 'output_duration_limit_seconds',
                                  'source_sample_rate_limit', 'source_channels_limit', 'output_sample_rate_limit','output_channels_limit', 'max_output_bytes', 'threads', 'timeout_seconds'])
def test_declared_audio_dimensions_cannot_exceed_calibrated_caps(tmp_path, field):
    budget = HostBudget(max_cpu_percent=100, min_available_memory_bytes=1, max_swap_used_bytes=10**12,
        min_free_disk_bytes=1, max_disk_io_bytes_per_second=10**12, max_unit_input_bytes=10**12,
        cpu_audio=audio_budget())
    probe = HostResourceProbe(tmp_path/'isolated.sqlite', disk_paths=(tmp_path,), budget=budget)
    resources = audio_resources()
    resources[field] = 10**15
    assert probe._sample(resources).verdict is False
    resources[field] = None
    assert probe._sample(resources).verdict is None


def test_missing_audio_budget_never_inherits_render_or_narration_budget(tmp_path):
    budget = HostBudget(max_cpu_percent=100, min_available_memory_bytes=1, max_swap_used_bytes=10**12,
        min_free_disk_bytes=1, max_disk_io_bytes_per_second=10**12, max_unit_input_bytes=10**12)
    probe = HostResourceProbe(tmp_path/'isolated.sqlite', disk_paths=(tmp_path,), budget=budget)
    decision = probe._sample(audio_resources())
    assert decision.verdict is None and decision.reason == 'audio_budget_or_execution_mode_unavailable'
