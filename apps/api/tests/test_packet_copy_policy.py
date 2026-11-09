"""Synthetic resource facts only: no native codec or user artifact is executed."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import psutil
import pytest

from app.packet_copy_policy import (PacketCopyBudget, PACKET_COPY_UNIT_PREFIX,
    packet_copy_resources, packet_copy_output_allocation, packet_copy_preparation_budget,
    packet_copy_remaining_allocations)
from app.host_resource_probes import HostBudget, HostResourceProbe
from app.device_scheduler import Grant, RecoveryBlocked
from app.device_resource_profile import (DeviceProfileUnavailable, host_budget_from_payload,
    load_device_resource_profile)
from tests.host_memory_boundary import IsolatedMemoryMonitor
from test_device_resource_profile import profile_document, write_profile, DEVICE, BACKENDS


def fixture(tmp_path, monkeypatch, *, mode='copy', output=33554433, published_target=None):
    budget = PacketCopyBudget('a'*64, 1024**2, 1024**2, 4, 1, .05, 65536, 262144, 65536, 20.)
    paths = {role: tmp_path / role for role in ('manifest', 'staging', 'published')}
    for path in paths.values():
        path.mkdir()
    files = {role: path / (role + '.fixture') for role, path in paths.items()}
    parents = [{'artifact_id': 'original-parent', 'sha256': 'b'*64, 'size_bytes': 2**30,
        'packet_count': 18000, 'producer_request_id': 'original-request', 'producer_generation': 3}]
    resources = packet_copy_resources(budget=budget, manifest_digest='c'*64, unit_id='d'*64,
        parents=parents, expected_total_packets=18000, write_paths=paths, write_files=files,
        output_limit_bytes=output, manifest_bytes=512, publication_mode=mode, published_target=published_target)
    host = HostBudget(100, 100, None, 200, None, 1, packet_copy=budget)
    probe = HostResourceProbe(tmp_path / 'db', disk_paths=(tmp_path,), budget=host,
        memory_monitor=IsolatedMemoryMonitor())
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=2**30))
    monkeypatch.setattr(psutil, 'swap_memory', lambda: SimpleNamespace(used=0))
    monkeypatch.setattr(psutil, 'disk_io_counters', lambda **kwargs: None)
    monkeypatch.setattr(psutil, 'cpu_percent', lambda **kwargs: 0)
    monkeypatch.setattr(psutil, 'disk_usage', lambda path: SimpleNamespace(free=2**40))
    return budget, resources, probe, paths, files


def test_packet_copy_admits_full_stream_without_encoding_caps_and_rejects_forged_facts(tmp_path, monkeypatch):
    budget, resources, probe, paths, files = fixture(tmp_path, monkeypatch)
    request = SimpleNamespace(id='copy', owner_type='creation_job',
        unit_key=PACKET_COPY_UNIT_PREFIX + resources['unit_id'], resources=resources)
    result = probe.admit(request)
    assert result.verdict is True, result.reason
    assert result.checks['packet_copy_policy']['source_bytes'] == 2**30
    assert resources['max_output_bytes'] > 32 * 1024**2
    assert result.checks['disk_io_bytes_per_second']['state'] == 'unknown'
    assert result.checks['packet_copy_policy']['throughput_verification'] == 'unknown'
    assert 'duration_seconds' not in resources and 'pixel_seconds' not in resources
    for name, value in [('source_bytes', 1), ('input_count', 0), ('timeout_seconds', 21.),
                        ('expected_total_packets', 1), ('threads', 2), ('max_stdout_bytes', 65537)]:
        assert probe._sample({**resources, name: value}).verdict is False
    forged = deepcopy(resources)
    forged['parents'][0]['producer_generation'] = None
    assert probe._sample(forged).verdict is False
    assert probe.admit(SimpleNamespace(**{**vars(request), 'owner_type': 'material_ingestion'})).reason == 'packet_copy_owner_invalid'
    assert probe.admit(SimpleNamespace(**{**vars(request), 'unit_key': 'render:whole-film'})).reason == 'packet_copy_owner_invalid'
    unavailable = HostResourceProbe(tmp_path/'other', disk_paths=(tmp_path,), budget=replace(probe.budget, packet_copy=None),
        memory_monitor=IsolatedMemoryMonitor())
    assert unavailable._sample(resources).verdict is None


def test_packet_copy_actual_three_volumes_and_remaining_write_allocation(tmp_path, monkeypatch):
    from app import device_resource_volumes as volumes, packet_copy_policy as policy
    budget, _resources, probe, paths, files = fixture(tmp_path, monkeypatch, output=1000)
    ids = {str(path): index for index, path in enumerate(paths.values())}
    def volume(path):
        return ids[str(path)], Path(path)
    monkeypatch.setattr(volumes, 'existing_volume', volume)
    monkeypatch.setattr(policy, 'existing_volume', volume)
    free = {str(path): 1200 for path in paths.values()}
    free[str(paths['manifest'])] = 712
    monkeypatch.setattr(psutil, 'disk_usage', lambda path: SimpleNamespace(free=free[str(path)]))
    amount = packet_copy_output_allocation(write_paths=paths, write_files=files, publication_mode='copy',
        manifest_bytes=512, reserve_bytes=200)
    assert amount == 1000
    resources = packet_copy_resources(budget=budget, manifest_digest='c'*64, unit_id='d'*64,
        parents=_resources['parents'], expected_total_packets=18000, write_paths=paths, write_files=files,
        output_limit_bytes=amount, manifest_bytes=512, publication_mode='copy')
    assert probe._sample(resources).verdict is True
    free[str(paths['staging'])] -= 1
    assert probe._sample(resources).verdict is False  # Other volumes cannot cover the short staging volume.
    free[str(paths['staging'])] += 1
    with pytest.raises(ValueError, match='same actual volume'):
        packet_copy_output_allocation(write_paths=paths, write_files=files, publication_mode='atomic_rename',
            manifest_bytes=512, reserve_bytes=200)
    files['manifest'].write_bytes(b'm' * 512)
    files['staging'].write_bytes(b's' * 700)
    free[str(paths['manifest'])] -= 512
    free[str(paths['staging'])] -= 700
    assert packet_copy_remaining_allocations(resources, running=True) == {'manifest': 0, 'staging': 300, 'published': 1000}
    assert probe._sample(resources, running_io=True).verdict is True  # No double charge of already-written bytes.
    assert probe._sample(resources).verdict is None  # New admission cannot reuse an unverified partial output.
    original = Path.lstat
    def unavailable(path, *args, **kwargs):
        if path == files['staging']:
            raise PermissionError('isolated unreadable partial')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', unavailable)
    assert probe._sample(resources, running_io=True).verdict is None


def test_atomic_packet_copy_reserves_one_live_output_and_preserves_unknown_volume(tmp_path, monkeypatch):
    budget, resources, probe, paths, files = fixture(tmp_path, monkeypatch, mode='atomic_rename', output=1000)
    monkeypatch.setattr(psutil, 'disk_usage', lambda path: SimpleNamespace(free=1712))
    assert packet_copy_output_allocation(write_paths=paths, write_files=files, publication_mode='atomic_rename',
        manifest_bytes=512, reserve_bytes=200) == 1000
    assert probe._sample(resources).verdict is True
    files['manifest'].write_bytes(b'm' * 512)
    files['staging'].write_bytes(b's' * 700)
    files['staging'].rename(files['published'])
    monkeypatch.setattr(psutil, 'disk_usage', lambda path: SimpleNamespace(free=500))
    assert probe._sample(resources, running_io=True).verdict is True
    files['staging'].write_bytes(b'not-an-atomic-rename')
    assert probe._sample(resources, running_io=True).verdict is None


def test_packet_copy_runtime_reads_original_grant_and_policy_profile_stays_unverified(tmp_path, monkeypatch):
    budget, resources, probe, paths, files = fixture(tmp_path, monkeypatch)
    grant = Grant('request', 'workspace', 'creation_job', 'original-job',
        PACKET_COPY_UNIT_PREFIX+resources['unit_id'], 'input', 1, 4, 'token', probe.database_path)
    with sqlite3.connect(probe.database_path) as conn:
        conn.execute('CREATE TABLE device_work_requests(id,workspace_id,owner_type,owner_id,unit_key,input_digest,'
                     'attempt_number,last_grant_generation,state,resources_json)')
        conn.execute('INSERT INTO device_work_requests VALUES(?,?,?,?,?,?,?,?,?,?)',
            (grant.request_id, grant.workspace_id, grant.owner_type, grant.owner_id, grant.unit_key,
             grant.input_digest, 1, 4, 'granted', json.dumps(resources)))
    limits = probe.scene_runtime_limits(grant)
    assert limits.max_process_rss_bytes == budget.max_process_rss_bytes
    assert limits.max_process_threads == budget.max_process_threads
    assert probe.continue_unit(grant) is True and probe.released(grant) is True
    with pytest.raises(RecoveryBlocked):
        probe.scene_runtime_limits(replace(grant, generation=5))
    with pytest.raises(RecoveryBlocked):
        probe.scene_runtime_limits(grant, purpose='audio_mix')
    document = profile_document()
    document['budget']['packet_copy'] = asdict(budget)
    document['backends']['packet_copy'] = budget.backend_identity
    path = write_profile(tmp_path, document)
    resolved = load_device_resource_profile(path, device=DEVICE,
        backend_identities={**BACKENDS, 'packet_copy': budget.backend_identity})
    assert resolved.budget.packet_copy == budget
    assert resolved.evidence['packet_copy'] == {'status': 'declared', 'verification_status': 'unknown'}
    changed = load_device_resource_profile(path, device=DEVICE, backend_identities={**BACKENDS, 'packet_copy': 'e'*64})
    assert changed.budget.packet_copy is None and changed.budget.cpu_render is not None
    assert host_budget_from_payload(asdict(resolved.budget)).packet_copy == budget
    candidate = packet_copy_preparation_budget(host_budget=resolved.budget, backend_identity='a'*64)
    assert candidate.preparation_timeout_seconds == resolved.budget.cpu_render.max_timeout_seconds
    assert not hasattr(candidate, 'max_output_bytes') and not hasattr(candidate, 'max_duration_seconds')
    document['budget']['packet_copy']['backend_identity'] = 'f'*64
    with pytest.raises(DeviceProfileUnavailable, match='disagree'):
        load_device_resource_profile(write_profile(tmp_path, document), device=DEVICE,
            backend_identities={**BACKENDS, 'packet_copy': 'a'*64})


def test_packet_copy_backend_identity_tracks_actual_tools_and_unknown_space_is_not_zero(tmp_path, monkeypatch):
    from app.device_resource_configuration import backend_identity
    from app import device_resource_configuration as configuration
    budget, resources, probe, paths, files = fixture(tmp_path, monkeypatch)
    tools = {}
    for name in ('ffmpeg', 'ffprobe'):
        path = tmp_path / name
        path.write_text('original-' + name)
        tools[name] = str(path)
    monkeypatch.setattr(configuration.shutil, 'which', lambda name: tools.get(name))
    original = backend_identity('packet_copy', None, settings=SimpleNamespace())
    assert original and len(original) == 64
    Path(tools['ffmpeg']).write_text('replaced-binary')
    assert backend_identity('packet_copy', None, settings=SimpleNamespace()) != original
    tools.pop('ffprobe')
    assert backend_identity('packet_copy', None, settings=SimpleNamespace()) is None
    monkeypatch.setattr(psutil, 'disk_usage', lambda path: SimpleNamespace(free=None))
    with pytest.raises(OSError, match='unknown'):
        packet_copy_output_allocation(write_paths=paths, write_files=files, publication_mode='copy',
            manifest_bytes=512, reserve_bytes=200)


def test_sealed_publication_target_finishes_output_allocation_across_link_and_unlink(tmp_path, monkeypatch):
    target = tmp_path/'published'/'final.mp4'
    budget, resources, probe, paths, files = fixture(tmp_path, monkeypatch, output=1000, published_target=target)
    assert resources['publication_target'] == {'path':str(target), 'directory':str(target.parent),
        'volume_id':target.parent.stat().st_dev}
    files['manifest'].write_bytes(b'm'*512)
    files['staging'].write_bytes(b's'*700)
    files['published'].write_bytes(b's'*700)
    assert packet_copy_remaining_allocations(resources, running=True) == {'manifest':0, 'staging':300, 'published':300}
    monkeypatch.setattr(psutil, 'disk_usage', lambda path: SimpleNamespace(free=200))
    assert probe._sample(resources, running_io=True).verdict is False
    # ArtifactStore closes and hashes the copy before linking its immutable final name.
    os.link(files['published'], target)
    assert packet_copy_remaining_allocations(resources, running=True) == {'manifest':0, 'staging':0, 'published':0}
    assert probe._sample(resources, running_io=True).verdict is True
    files['published'].unlink()
    assert packet_copy_remaining_allocations(resources, running=True) == {'manifest':0, 'staging':0, 'published':0}
    assert probe._sample(resources, running_io=True).verdict is True
    assert resources['max_output_bytes'] == 1000 and resources['write_allocations']['published'] == 1000
    assert probe._sample(resources).verdict is not True  # A new admission cannot adopt this existing output.
    with pytest.raises(ValueError, match='overwrite'):
        packet_copy_output_allocation(write_paths=paths, write_files=files, publication_mode='copy',
            manifest_bytes=512, reserve_bytes=200, published_target=target)


@pytest.mark.parametrize('failure', ['directory', 'symlink', 'foreign_link', 'different_copy', 'third_link',
    'oversize_target', 'oversize_staging', 'empty_target', 'wrong_volume', 'changed_digest'])
def test_sealed_publication_rejects_unknown_or_unowned_final_files(tmp_path, monkeypatch, failure):
    target = tmp_path/'published'/'final.mp4'
    _budget, resources, probe, _paths, files = fixture(tmp_path, monkeypatch, output=1000, published_target=target)
    if failure == 'directory':
        target.mkdir()
    elif failure in {'symlink', 'foreign_link'}:
        other = tmp_path/'unrelated-file'
        other.write_bytes(b'not the publication')
        if failure == 'symlink':
            target.symlink_to(other)
        else:
            os.link(other, target)
    else:
        target.write_bytes(b't'*700)
        if failure == 'different_copy':
            files['published'].write_bytes(b't'*700)
        elif failure == 'third_link':
            os.link(target, files['published'])
            os.link(target, tmp_path/'extra-hardlink')
        elif failure == 'oversize_target':
            target.write_bytes(b'x'*1001)
        elif failure == 'oversize_staging':
            files['staging'].write_bytes(b'x'*1001)
        elif failure == 'empty_target':
            target.write_bytes(b'')
        elif failure == 'wrong_volume':
            resources['publication_target']['volume_id'] += 1
        else:
            resources['publication_target_digest'] = '0'*64
    assert probe._sample(resources, running_io=True).verdict is not True
    with pytest.raises((ValueError, OSError)):
        packet_copy_remaining_allocations(resources, running=True)


def test_missing_or_unsealed_target_does_not_complete_old_output_allocation(tmp_path, monkeypatch):
    target = tmp_path/'published'/'final.mp4'
    _budget, resources, _probe, _paths, files = fixture(tmp_path, monkeypatch, output=1000)
    assert 'publication_target' not in resources and 'publication_target_digest' not in resources
    files['staging'].write_bytes(b's'*700)
    before = packet_copy_remaining_allocations(resources, running=True)
    target.write_bytes(b't'*700)
    assert packet_copy_remaining_allocations(resources, running=True) == before == {
        'manifest':512, 'staging':300, 'published':1000}


@pytest.mark.parametrize('failure', ['outside', 'same_copy', 'parent'])
def test_new_publication_target_must_be_its_own_declared_directory_slot(tmp_path, monkeypatch, failure):
    budget, resources, _probe, paths, files = fixture(tmp_path, monkeypatch, output=1000)
    target = {'outside':tmp_path/'outside.mp4', 'same_copy':files['published'], 'parent':paths['published']/'..'}[failure]
    with pytest.raises(ValueError, match='sealed publication'):
        packet_copy_resources(budget=budget, manifest_digest='c'*64, unit_id='d'*64,
            parents=resources['parents'], expected_total_packets=18000, write_paths=paths, write_files=files,
            output_limit_bytes=1000, manifest_bytes=512, publication_mode='copy', published_target=target)
