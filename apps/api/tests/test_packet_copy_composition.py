"""Explicit normal-profile composition; no runtime, probe or media work."""
from dataclasses import asdict, replace

from app.config import Settings
from app.device_resource_configuration import resolve_device_resources, with_packet_copy_preparation
from app.packet_copy_policy import packet_copy_preparation_budget
from test_device_resource_profile import DEVICE, BACKENDS, profile_document, write_profile
from test_device_runtime_wiring import _CALIBRATED_ENV


def test_normal_copy_selection_is_explicit_deterministic_and_preserves_source_file(tmp_path):
    document = profile_document()
    path = write_profile(tmp_path, document)
    settings = Settings(device_resource_profile_path=str(path))
    identities = {**BACKENDS, 'packet_copy': 'a'*64}
    def resolve(**kwargs):
        return resolve_device_resources(settings, {}, device=DEVICE, backend_identities=identities, **kwargs)
    source = resolve()
    source_bytes = path.read_bytes()
    selected = resolve(select_packet_copy_preparation=True)
    again = resolve(select_packet_copy_preparation=True)
    assert source.budget.packet_copy is None and selected.budget.packet_copy is not None
    assert selected == again
    assert selected.budget.packet_copy.backend_identity == 'a'*64
    assert selected.profile_id == source.profile_id and selected.revision == source.revision
    assert selected.source_kind == source.source_kind == 'device_profile'
    assert selected.digest != source.digest and path.read_bytes() == source_bytes
    proof = selected.evidence['packet_copy']
    assert proof['status'] == 'declared' and proof['verification_status'] == 'unknown'
    assert proof['throughput_verification'] == 'unknown'
    assert proof['source_profile_digest'] == source.digest
    assert proof['source_operation'] == 'cpu_render'
    assert selected.evidence['cpu_render'] == source.evidence['cpu_render']
    assert with_packet_copy_preparation(selected, backend_identity='a'*64) is selected
    identities['packet_copy'] = 'b'*64
    assert resolve(select_packet_copy_preparation=True).digest != selected.digest
    identities['packet_copy'] = 'a'*64
    document['budget']['cpu_render']['max_timeout_seconds'] += 1
    write_profile(tmp_path, document)
    changed = resolve(select_packet_copy_preparation=True)
    assert changed.digest != selected.digest
    assert changed.evidence['packet_copy']['source_operation_budget_digest'] != proof['source_operation_budget_digest']
    document['revision'] += 1
    write_profile(tmp_path, document)
    assert resolve(select_packet_copy_preparation=True).digest != changed.digest


def test_explicit_copy_profile_or_backend_failure_is_not_replaced_by_cpu_candidate(tmp_path):
    document = profile_document()
    path = write_profile(tmp_path, document)
    settings = Settings(device_resource_profile_path=str(path))
    source = resolve_device_resources(settings, {}, device=DEVICE, backend_identities=BACKENDS)
    explicit = replace(packet_copy_preparation_budget(host_budget=source.budget, backend_identity='a'*64),
        preparation_timeout_seconds=17.)
    document['budget']['packet_copy'] = asdict(explicit)
    document['backends']['packet_copy'] = 'a'*64
    write_profile(tmp_path, document)
    chosen = resolve_device_resources(settings, {}, device=DEVICE,
        backend_identities={**BACKENDS, 'packet_copy': 'a'*64}, select_packet_copy_preparation=True)
    assert chosen.budget.packet_copy == explicit
    assert chosen.evidence['packet_copy'] == {'status': 'declared', 'verification_status': 'unknown'}
    invalid = resolve_device_resources(settings, {}, device=DEVICE,
        backend_identities={**BACKENDS, 'packet_copy': 'b'*64}, select_packet_copy_preparation=True)
    assert invalid.budget.packet_copy is None and invalid.budget.cpu_render is not None
    assert invalid.unavailable['packet_copy'] == 'backend_identity_changed_or_unavailable'
    assert with_packet_copy_preparation(invalid, backend_identity='b'*64) is invalid


def test_missing_copy_tools_or_cpu_policy_only_disables_copy_and_experiment_is_unchanged(tmp_path):
    path = write_profile(tmp_path, profile_document())
    settings = Settings(device_resource_profile_path=str(path))
    calls = []
    def identities(name, budget):
        calls.append(name)
        if name == 'packet_copy':
            raise OSError('/private/path/should-not-be-exposed')
        return BACKENDS.get(name)
    selected = resolve_device_resources(settings, {}, device=DEVICE,
        backend_identities=identities, select_packet_copy_preparation=True)
    assert selected.budget.cpu_render is not None and selected.budget.narration is not None
    assert selected.budget.packet_copy is None
    assert selected.unavailable['packet_copy'] == 'packet_copy_backend_identity_unavailable'
    no_cpu = resolve_device_resources(settings, {}, device=DEVICE,
        backend_identities={**BACKENDS, 'cpu_render': 'changed', 'packet_copy': 'a'*64},
        select_packet_copy_preparation=True)
    assert no_cpu.budget.narration is not None and no_cpu.budget.packet_copy is None
    assert no_cpu.unavailable['packet_copy'] == 'packet_copy_process_policy_unavailable'
    source = resolve_device_resources(settings, {}, device=DEVICE, backend_identities=BACKENDS)
    incompatible = replace(source, budget=replace(source.budget, min_free_disk_bytes=0))
    refused = with_packet_copy_preparation(incompatible, backend_identity='a'*64)
    assert refused.budget == incompatible.budget
    assert refused.unavailable['packet_copy'] == 'packet_copy_preparation_policy_unavailable'
    experiment = Settings(device_resource_mode='experiment', device_resource_experiment_id='fixture')
    ordinary = resolve_device_resources(experiment, _CALIBRATED_ENV)
    with_selection = resolve_device_resources(experiment, _CALIBRATED_ENV,
        backend_identities=lambda *a: (_ for _ in ()).throw(AssertionError('no implicit observation')),
        select_packet_copy_preparation=True)
    assert ordinary == with_selection and with_selection.budget.packet_copy is None
