"""Audio delivery declarations: no codec, model or user database execution."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import psutil
import pytest

from app.audio_delivery_policy import (AudioEncodeBudget, FinalMuxBudget, DELIVERY_KINDS,
    audio_encode_resources, final_mux_resources, audio_delivery_refusal)
from app.config import Settings
from app.device_resource_configuration import resolve_device_resources
from app.device_resource_profile import host_budget_from_payload
from app.device_scheduler import Grant, RecoveryBlocked
from app.host_resource_probes import HostBudget, HostResourceProbe, CpuAudioBudget
from tests.host_memory_boundary import IsolatedMemoryMonitor
from test_device_resource_profile import DEVICE, BACKENDS, profile_document, write_profile
from test_device_runtime_wiring import _CALIBRATED_ENV


def parent(clock, name='master'):
    return {'binding': {'artifact_id': name, 'sha256': 'b'*64, 'size_bytes': 2**30,
        'producer_request_id': 'original-'+name, 'producer_generation': 3,
        'transform_id': 'transform-'+name, 'transform_digest': 'c'*64}, 'clock': clock}


def fixture(tmp_path, monkeypatch, operation='audio_encode'):
    kind = AudioEncodeBudget if operation == 'audio_encode' else FinalMuxBudget
    budget = kind('a'*64, 1024**2, 1024**2, 4, 1, .05, 65536, 262144, 65536, 20.)
    paths = {role: tmp_path/role for role in ('manifest', 'staging', 'published')}
    for path in paths.values():
        path.mkdir()
    files = {role: path/(role+'.fixture') for role, path in paths.items()}
    common = dict(budget=budget, manifest_digest='d'*64, unit_id='e'*64,
        write_paths=paths, write_files=files, output_limit_bytes=33554433, manifest_bytes=2048,
        publication_mode='copy')
    if operation == 'audio_encode':
        resources = audio_encode_resources(**common,
            master=parent(dict(sample_count=48000*1800, sample_rate=48000, channels=2, sample_format='s16le')),
            audio_recipe=dict(codec='aac', sample_rate=48000, channels=2, parameters_digest='f'*64, fragmented=True))
    else:
        clock = dict(packet_count=20, time_base_num=1, time_base_den=48000, first_pts=-1024, last_end_pts=19200)
        resources = final_mux_resources(**common, picture=parent(dict(packet_count=12,
            time_base_num=1, time_base_den=30, first_pts=0, last_end_pts=12), 'picture'),
            encoded_audio=parent({**clock, 'sample_count':19200, 'sample_rate':48000, 'channels':2,
                'initial_skip_samples':1024, 'trailing_padding_samples':None}, 'aac'),
            mux_recipe=dict(video='copy', audio='copy', parameters_digest='f'*64, fragmented=False, faststart=True))
    host = HostBudget(100, 100, None, 200, None, 1, **{operation:budget})
    probe = HostResourceProbe(tmp_path/'db', disk_paths=(tmp_path,), budget=host,
        memory_monitor=IsolatedMemoryMonitor())
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=2**30))
    monkeypatch.setattr(psutil, 'swap_memory', lambda: SimpleNamespace(used=0))
    monkeypatch.setattr(psutil, 'disk_io_counters', lambda **kwargs: None)
    monkeypatch.setattr(psutil, 'cpu_percent', lambda **kwargs: 0)
    monkeypatch.setattr(psutil, 'disk_usage', lambda path: SimpleNamespace(free=2**40))
    return budget, resources, probe, paths, files


@pytest.mark.parametrize('operation', ['audio_encode', 'final_mux'])
def test_delivery_preserves_real_stream_work_and_original_grant_without_film_caps(tmp_path, monkeypatch, operation):
    budget, resources, probe, paths, files = fixture(tmp_path, monkeypatch, operation)
    unit = DELIVERY_KINDS[resources['kind']][1] + resources['unit_id']
    request = SimpleNamespace(id='request', owner_type='creation_job', unit_key=unit, resources=resources)
    result = probe.admit(request)
    assert result.verdict is True, result.reason
    assert resources['source_bytes'] == resources['input_count']*2**30
    assert result.checks['audio_delivery_policy']['throughput_verification'] == 'unknown'
    assert 'pixel_seconds' not in resources and resources['max_output_bytes'] > 32*1024**2
    assert result.checks['disk_io_bytes_per_second']['state'] == 'unknown'
    if operation == 'final_mux':
        clock = resources['parents'][1]['clock']
        assert clock['first_pts'] == -1024 and clock['sample_count'] == 19200
        assert clock['trailing_padding_samples'] is None
    for field, value in [('source_bytes', 1), ('input_count', 0), ('timeout_seconds', 21), ('threads', 2)]:
        assert probe._sample({**resources, field:value}).verdict is False
    assert probe.admit(SimpleNamespace(**{**vars(request), 'unit_key':'render:packet-copy:v1:'+resources['unit_id']})).reason == 'audio_delivery_owner_invalid'
    absent = replace(probe.budget, **{operation:None})
    assert HostResourceProbe(tmp_path/'missing', disk_paths=(tmp_path,), budget=absent,
        memory_monitor=IsolatedMemoryMonitor())._sample(resources).verdict is None
    grant = Grant('request', 'workspace', 'creation_job', 'original-job', unit, 'input', 1, 4, 'token', probe.database_path)
    with sqlite3.connect(probe.database_path) as conn:
        conn.execute('CREATE TABLE device_work_requests(id,workspace_id,owner_type,owner_id,unit_key,input_digest,'
                     'attempt_number,last_grant_generation,state,resources_json)')
        conn.execute('INSERT INTO device_work_requests VALUES(?,?,?,?,?,?,?,?,?,?)',
            (grant.request_id, grant.workspace_id, grant.owner_type, grant.owner_id, grant.unit_key,
             grant.input_digest, 1, 4, 'granted', json.dumps(resources)))
    assert probe.scene_runtime_limits(grant, purpose=operation).max_process_rss_bytes == budget.max_process_rss_bytes
    assert probe.continue_unit(grant) is True and probe.released(grant) is True
    with pytest.raises(RecoveryBlocked):
        probe.scene_runtime_limits(replace(grant, generation=5))
    with pytest.raises(RecoveryBlocked):
        probe.scene_runtime_limits(grant, purpose='packet_copy')
    # A codec recipe cannot turn the copy unit into another encode pass.
    if operation == 'final_mux':
        bad = deepcopy(resources); bad['mux_recipe']['audio'] = 'aac'
        assert audio_delivery_refusal(bad, budget) == 'audio_delivery_resources_invalid'


def test_delivery_new_outputs_use_actual_volumes_and_unknown_space_stays_unknown(tmp_path, monkeypatch):
    from app import device_resource_volumes, packet_copy_policy
    budget, resources, probe, paths, files = fixture(tmp_path, monkeypatch)
    ids = {str(path): index for index, path in enumerate(paths.values())}
    volume = lambda path: (ids[str(path)], Path(path))
    monkeypatch.setattr(device_resource_volumes, 'existing_volume', volume)
    monkeypatch.setattr(packet_copy_policy, 'existing_volume', volume)
    resources['write_volume_ids'] = {role: ids[str(path)] for role, path in paths.items()}
    free = {str(path): resources['write_allocations'][role]+200 for role, path in paths.items()}
    monkeypatch.setattr(psutil, 'disk_usage', lambda path: SimpleNamespace(free=free[str(path)]))
    assert probe._sample(resources).verdict is True
    free[str(paths['published'])] -= 1
    assert probe._sample(resources).verdict is False
    for unknown in (None, True, -1, 'unknown'):
        free[str(paths['published'])] = unknown
        assert probe._sample(resources).verdict is None
    free[str(paths['published'])] = 0
    assert probe._sample(resources).verdict is False
    free[str(paths['published'])] = resources['max_output_bytes']+200
    files['staging'].write_bytes(b'current-output')
    free[str(paths['staging'])] -= len(b'current-output')
    assert probe._sample(resources, running_io=True).verdict is True


def test_delivery_normal_first_use_is_explicit_unverified_and_invalid_explicit_policy_is_not_replaced(tmp_path):
    document = profile_document(); path = write_profile(tmp_path, document)
    settings = Settings(device_resource_profile_path=str(path))
    identities = {**BACKENDS, 'audio_encode':'a'*64, 'final_mux':'b'*64}
    resolve = lambda **kw: resolve_device_resources(settings, {}, device=DEVICE, backend_identities=identities, **kw)
    ordinary = resolve(); original = path.read_bytes()
    selected = resolve(select_audio_delivery_preparation=True)
    assert selected == resolve(select_audio_delivery_preparation=True)
    assert ordinary.budget.audio_encode is None and ordinary.budget.final_mux is None
    assert selected.source_kind == ordinary.source_kind == 'device_profile'
    assert selected.digest != ordinary.digest and path.read_bytes() == original
    assert host_budget_from_payload(asdict(selected.budget)) == selected.budget
    for operation in ('audio_encode', 'final_mux'):
        budget = getattr(selected.budget, operation)
        assert budget.preparation_timeout_seconds == ordinary.budget.cpu_render.max_timeout_seconds
        assert selected.evidence[operation]['verification_status'] == 'unknown'
        assert selected.evidence[operation]['throughput_verification'] == 'unknown'
        assert not hasattr(budget, 'max_output_bytes') and not hasattr(budget, 'max_duration_seconds')
    document['budget']['audio_encode'] = asdict(selected.budget.audio_encode)
    document['backends']['audio_encode'] = 'a'*64
    write_profile(tmp_path, document); identities['audio_encode'] = 'c'*64
    invalid = resolve(select_audio_delivery_preparation=True)
    assert invalid.budget.audio_encode is None and invalid.budget.final_mux is not None
    assert invalid.unavailable['audio_encode'] == 'backend_identity_changed_or_unavailable'
    identities['audio_encode'] = 'a'*64
    document['budget']['audio_encode']['backend_identity'] = 'd'*64
    write_profile(tmp_path, document)
    assert resolve(select_audio_delivery_preparation=True).budget.audio_encode is None
    experiment = Settings(device_resource_mode='experiment', device_resource_experiment_id='fixture')
    assert resolve_device_resources(experiment, _CALIBRATED_ENV) == resolve_device_resources(
        experiment, _CALIBRATED_ENV, select_audio_delivery_preparation=True,
        backend_identities=lambda *a: pytest.fail('experiment must not select normal policy'))


def test_delivery_actual_tool_identity_and_audio_only_process_source(tmp_path, monkeypatch):
    from app import device_resource_configuration as configuration
    from app.audio_delivery_policy import audio_delivery_preparation_budget
    tools = {}
    for name in ('ffmpeg', 'ffprobe'):
        path = tmp_path/name; path.write_text('original-'+name); tools[name] = str(path)
    monkeypatch.setattr(configuration.shutil, 'which', lambda name:tools.get(name))
    original = configuration.backend_identity('audio_encode', None, settings=SimpleNamespace())
    assert original and len(original) == 64
    assert configuration.backend_identity('final_mux', None, settings=SimpleNamespace()) != original
    Path(tools['ffmpeg']).write_text('different-native-binary')
    assert configuration.backend_identity('audio_encode', None, settings=SimpleNamespace()) != original
    tools.pop('ffprobe')
    assert configuration.backend_identity('audio_encode', None, settings=SimpleNamespace()) is None
    settings = Settings(device_resource_mode='experiment', device_resource_experiment_id='fixture')
    source = CpuAudioBudget(2, 4096, 1., 1., 24000, 1, 4096, 1, 3.,
        256*1024*1024, 16, .05, 256*1024*1024, 4096)
    host = replace(resolve_device_resources(settings, _CALIBRATED_ENV).budget, cpu_render=None, cpu_audio=source)
    assert host.cpu_audio is not None
    audio = audio_delivery_preparation_budget(operation='audio_encode', host_budget=host, backend_identity='a'*64)
    mux = audio_delivery_preparation_budget(operation='final_mux', host_budget=host, backend_identity='b'*64)
    assert audio.max_process_rss_bytes == mux.max_process_rss_bytes == host.cpu_audio.max_process_rss_bytes
    assert audio.preparation_timeout_seconds == host.cpu_audio.max_timeout_seconds
