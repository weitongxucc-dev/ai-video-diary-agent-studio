from types import SimpleNamespace

import pytest

from app import device_resource_volumes as volumes


def test_same_volume_sums_live_temporary_and_output_bytes_once_per_floor(tmp_path, monkeypatch):
    paths = {'database': tmp_path / 'db', 'output': tmp_path / 'out', 'temporary': tmp_path / 'tmp'}
    monkeypatch.setattr(volumes.psutil, 'disk_usage', lambda path: SimpleNamespace(free=699))
    result = volumes.sample_write_volumes(paths, {'database': 0, 'output': 200, 'temporary': 400}, reserve_bytes=100)
    assert all(item['minimum'] == 700 and item['passed'] is False for item in result.values())
    assert all(item['roles'] == ['database', 'output', 'temporary'] for item in result.values())
    assert not any(path.exists() for path in paths.values())


def test_distinct_volumes_do_not_pool_free_space_and_are_resampled(tmp_path, monkeypatch):
    paths = {'database': tmp_path / 'db', 'output': tmp_path / 'out', 'temporary': tmp_path / 'tmp'}
    ids = {path: index for index, path in enumerate(paths.values())}
    monkeypatch.setattr(volumes, 'existing_volume', lambda path: (ids[path], path))
    free = {str(paths['database']): 100, str(paths['output']): 10000, str(paths['temporary']): 499}
    monkeypatch.setattr(volumes.psutil, 'disk_usage', lambda path: SimpleNamespace(free=free[path]))
    first = volumes.sample_write_volumes(paths, {'database': 0, 'output': 200, 'temporary': 400}, reserve_bytes=100)
    assert first['output']['passed'] and not first['temporary']['passed']
    free[str(paths['temporary'])] = 500
    assert volumes.sample_write_volumes(paths, {'database': 0, 'output': 200, 'temporary': 400}, reserve_bytes=100)['temporary']['passed']


def test_preparation_exercises_actual_directory_write_without_leaving_probe(tmp_path):
    path = tmp_path / 'new-output'
    result = volumes.sample_write_volumes({'output': path}, {'output': 1}, reserve_bytes=0, verify_writable=True)
    assert result['output']['passed']
    assert path.is_dir() and list(path.iterdir()) == []


def test_granted_observation_keeps_unknown_and_incomplete_distinct():
    from dataclasses import FrozenInstanceError
    from app.granted_subprocess import _observed_command_resources
    unknown = _observed_command_resources({})
    assert unknown.peak_rss_bytes is None and unknown.peak_threads is None
    assert not unknown.rss_sample_complete
    incomplete = _observed_command_resources({'observed_peak_rss': {'rss_bytes': 123, 'sample_complete': False}})
    assert incomplete.peak_rss_bytes == 123 and not incomplete.rss_sample_complete
    with pytest.raises(FrozenInstanceError):
        incomplete.peak_rss_bytes = 0


@pytest.mark.parametrize('threshold', [None, 100])
def test_io_observation_only_does_not_invent_throughput_capacity(tmp_path, monkeypatch, threshold):
    import app.host_resource_probes as host
    from tests.host_memory_boundary import IsolatedMemoryMonitor
    budget = host.HostBudget(100, 100, None, 200, threshold, 4096, .01)
    probe = host.HostResourceProbe(tmp_path / 'db.sqlite', disk_paths=(tmp_path,), budget=budget,
        memory_monitor=IsolatedMemoryMonitor())
    calls = iter([SimpleNamespace(read_bytes=0, write_bytes=0), SimpleNamespace(read_bytes=1000, write_bytes=0)])
    monkeypatch.setattr(host.psutil, 'disk_io_counters', lambda **kw: None if threshold is None else next(calls))
    monkeypatch.setattr(host.psutil, 'cpu_percent', lambda **kw: 0)
    monkeypatch.setattr(host.psutil, 'disk_usage', lambda path: SimpleNamespace(free=100000))
    ticks = iter([1.0, 2.0])
    monkeypatch.setattr(host.time, 'monotonic', lambda: next(ticks))
    decision = probe._sample({'kind': 'material_hash', 'size_bytes': 10})
    if threshold is None:
        assert decision.verdict is True
        assert decision.checks['disk_io_bytes_per_second']['actual'] is None
        assert decision.checks['disk_io_bytes_per_second']['diagnostic_only'] is True
    else:
        assert decision.verdict is False
        assert decision.checks['disk_io_bytes_per_second']['maximum'] == 100
        assert decision.checks['disk_io_bytes_per_second']['actual'] == 1000
