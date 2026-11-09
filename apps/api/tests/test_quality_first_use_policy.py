"""Finite quality declarations and real file identities; never run a worker."""
from dataclasses import asdict, replace
import hashlib
import subprocess
from types import SimpleNamespace

import pytest

from app.config import Settings
from app import device_resource_configuration as configuration
from app.device_resource_profile import DeviceProfileUnavailable, host_budget_from_payload
from app.quality_checks import fit_quality_result
from app.quality_preparation_policy import QUALITY_FIRST_USE_PACKET_BYTES, QUALITY_FIRST_USE_POLICY
from app.quality_worker import worker_identity
from app.quality_worker_runtime import QUALITY_PHASES, quality_stream_resources, quality_request_byte_limit
from test_device_resource_profile import DEVICE, BACKENDS, profile_document, write_profile
from test_device_runtime_wiring import _CALIBRATED_ENV


@pytest.fixture
def first_use(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('quality policy selection cannot start a process, model, probe or driver')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    paths = {}
    for name in ('ffmpeg', 'ffprobe'):
        path = tmp_path / name
        path.write_bytes(('declared identity fixture, never execute: ' + name).encode())
        path.chmod(0o700)
        paths[name] = path
    monkeypatch.setattr(configuration.shutil, 'which', lambda name: str(paths[name]))
    document = profile_document()
    path = write_profile(tmp_path, document)
    settings = Settings(device_resource_profile_path=str(path))
    def identities(name, budget):
        return (BACKENDS[name] if name in BACKENDS else
            configuration.backend_identity(name, budget, settings=settings))
    def resolve(**kwargs):
        return configuration.resolve_device_resources(settings, {}, device=DEVICE,
            backend_identities=identities, model_catalog=SimpleNamespace(status=forbidden), **kwargs)
    return SimpleNamespace(document=document, path=path, settings=settings, paths=paths,
        identities=identities, resolve=resolve, forbidden=forbidden)


def test_normal_quality_first_use_is_explicit_finite_and_never_rewrites_the_profile(first_use):
    case = first_use
    original = case.resolve()
    source_bytes = case.path.read_bytes()
    selected = case.resolve(select_quality_preparation=True)
    assert original.budget.quality is None
    assert selected == case.resolve(select_quality_preparation=True)
    assert case.path.read_bytes() == source_bytes
    assert (selected.profile_id, selected.revision, selected.source_kind) == (
        original.profile_id, original.revision, 'device_profile')
    assert selected.digest != original.digest
    assert selected.budget.cpu_render == original.budget.cpu_render
    assert selected.evidence['cpu_render'] == original.evidence['cpu_render']
    cap, cpu = selected.budget.quality, original.budget.cpu_render
    assert cap.max_timeout_seconds == cpu.max_timeout_seconds
    assert cap.max_process_rss_bytes == cap.additional_memory_bytes == cpu.additional_memory_bytes
    assert cap.max_process_threads == cpu.max_process_threads and cap.decoder_threads == 1
    assert cap.runtime_sample_seconds == original.budget.sample_seconds
    assert cap.max_input_bytes == min(cpu.max_input_bytes, original.budget.max_unit_input_bytes)
    assert cap.max_report_bytes == cap.max_stderr_bytes == cap.max_pcm_bytes == cap.max_frame_json_bytes == 65536
    assert cap.additional_disk_bytes == (len(QUALITY_PHASES) + 1) * cap.max_report_bytes
    assert cap.max_tool_bytes == max(path.stat().st_size for path in case.paths.values())
    for name, path in case.paths.items():
        assert getattr(cap, name + '_path') == str(path.resolve())
        assert getattr(cap, name + '_sha256') == hashlib.sha256(path.read_bytes()).hexdigest()
    assert cap.worker_sha256 == worker_identity()
    assert host_budget_from_payload(asdict(selected.budget)) == selected.budget
    proof = selected.evidence['quality']
    assert proof['status'] == 'declared' and proof['verification_status'] == 'unknown'
    assert proof['quality_measurement'] == 'not_performed' and proof['throughput_verification'] == 'unknown'
    assert proof['policy_id'] == QUALITY_FIRST_USE_POLICY and proof['protocol'] == 'quality_stream_v1'
    assert proof['source_operation'] == 'cpu_render' and proof['source_profile_digest'] == original.digest
    assert proof['backend_identity'] == case.identities('quality', cap)


def test_first_use_packets_do_not_become_source_or_decoded_work_caps(first_use):
    budget = first_use.resolve(select_quality_preparation=True).budget.quality
    request = {'budget': asdict(budget), 'protocol': 'quality_stream_v1',
        'source': {'render_ref': 'a' * 64, 'size_bytes': budget.max_input_bytes + 1}}
    resources = quality_stream_resources(request, 'metadata', {})
    assert resources['read_mode'] == 'stream'
    assert resources['source_bytes'] == resources['input_bytes'] == request['source']['size_bytes']
    assert resources['workload_provenance']['coverage'] == 'complete_stream_required'
    assert resources['timeout_seconds'] == budget.max_timeout_seconds
    assert quality_request_byte_limit(request) == len(QUALITY_PHASES) * QUALITY_FIRST_USE_PACKET_BYTES
    with pytest.raises(ValueError, match='必要来源.*不能截断'):
        fit_quality_result({'data': {'immutable_identity': 'x' * budget.max_report_bytes}}, budget.max_report_bytes)


def test_quality_composes_with_the_existing_normal_delivery_first_use_policies(first_use):
    original = first_use.path.read_bytes()
    selected = first_use.resolve(select_packet_copy_preparation=True,
        select_audio_delivery_preparation=True, select_quality_preparation=True)
    assert all(getattr(selected.budget, name) is not None
        for name in ('packet_copy', 'audio_encode', 'final_mux', 'quality'))
    assert all(selected.evidence[name]['verification_status'] == 'unknown'
        for name in ('packet_copy', 'audio_encode', 'final_mux', 'quality'))
    assert first_use.path.read_bytes() == original


def test_explicit_quality_is_preserved_and_changed_native_identity_is_not_replaced(first_use, tmp_path):
    case = first_use
    selected = case.resolve(select_quality_preparation=True)
    explicit = replace(selected.budget.quality, max_timeout_seconds=7.)
    case.document['budget']['quality'] = asdict(explicit)
    case.document['backends']['quality'] = case.identities('quality', explicit)
    write_profile(tmp_path, case.document)
    source_bytes = case.path.read_bytes()
    chosen = case.resolve(select_quality_preparation=True)
    assert chosen.budget.quality == explicit
    assert chosen.evidence['quality'] == {'status': 'declared', 'verification_status': 'unknown'}
    case.paths['ffmpeg'].write_bytes(b'changed installed quality tool identity')
    invalid = case.resolve(select_quality_preparation=True)
    assert invalid.budget.quality is None and invalid.budget.cpu_render is not None
    assert invalid.unavailable['quality'] == 'backend_identity_changed_or_unavailable'
    assert case.path.read_bytes() == source_bytes


def test_first_use_rebinds_changed_tools_in_its_own_budget_and_digest(first_use):
    original = first_use.resolve(select_quality_preparation=True)
    first_use.paths['ffprobe'].write_bytes(b'a different installed tool with independent byte size')
    selected = first_use.resolve(select_quality_preparation=True)
    assert selected.budget.quality.ffprobe_sha256 != original.budget.quality.ffprobe_sha256
    assert selected.evidence['quality']['backend_identity'] != original.evidence['quality']['backend_identity']
    assert selected.digest != original.digest
    assert configuration.backend_identity('quality', original.budget.quality, settings=first_use.settings) is None


@pytest.mark.parametrize('missing', ['tool', 'cpu', 'memory'])
def test_missing_first_use_prerequisites_disable_only_quality(first_use, monkeypatch, tmp_path, missing):
    case = first_use
    if missing == 'tool':
        monkeypatch.setattr(configuration.shutil, 'which', lambda name: None)
        reason = 'quality_first_use_policy_or_backend_unavailable'
    else:
        if missing == 'cpu':
            case.document['backends']['cpu_render'] = 'unmatched-original-cpu'
        else:
            case.document['budget']['memory_policy'] = None
        write_profile(tmp_path, case.document)
        monkeypatch.setattr(configuration, '_quality_first_use_tools', case.forbidden)
        reason = 'quality_process_policy_unavailable'
    selected = case.resolve(select_quality_preparation=True)
    assert selected.budget.quality is None and selected.budget.narration is not None
    assert selected.unavailable['quality'] == reason


def test_quality_first_use_does_not_change_experiment_or_inspect_tools(first_use, monkeypatch):
    experiment = Settings(device_resource_mode='experiment', device_resource_experiment_id='quality-fixture')
    monkeypatch.setattr(configuration, '_quality_first_use_tools', first_use.forbidden)
    source = configuration.resolve_device_resources(experiment, _CALIBRATED_ENV)
    selected = configuration.resolve_device_resources(experiment, _CALIBRATED_ENV,
        backend_identities=first_use.forbidden, select_quality_preparation=True)
    assert selected == source


def test_quality_first_use_requires_an_explicit_boolean(first_use):
    with pytest.raises(DeviceProfileUnavailable, match='显式指定'):
        first_use.resolve(select_quality_preparation='yes')
