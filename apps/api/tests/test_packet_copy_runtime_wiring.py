"""The real runtime shares the selected copy policy without launching work."""
from dataclasses import replace

from app import main
from app.device_resource_configuration import resolve_device_resources
from app.production import ProductionRepository
from test_device_resource_profile import BACKENDS, DEVICE, profile_document, write_profile


def test_packet_copy_runtime_uses_one_selected_profile_and_keeps_active_revision(tmp_path, monkeypatch):
    path = write_profile(tmp_path, profile_document())
    repository = ProductionRepository(tmp_path / 'runtime.sqlite', tmp_path / 'library')
    monkeypatch.setattr(main, 'settings', replace(main.settings,
        device_resource_mode='normal', device_resource_profile_path=str(path)))
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'material_execution_mode', 'manual')
    identities = {**BACKENDS, 'packet_copy': 'a' * 64}
    monkeypatch.setattr(main, 'resolve_device_resources', lambda settings, environment, **values:
        resolve_device_resources(settings, environment, device=DEVICE,
            backend_identities=identities, **values))

    runtime, material = main._build_device_components(repository=repository)
    creation = runtime.registrations['creation_job']
    service = creation.owner.packet_copy_assembly
    profile = runtime.resource_profile
    assert service is not None and service is creation.executor.packet_copy_assembly
    assert service.repository is repository
    assert service.budget is profile.budget.packet_copy
    assert runtime.host_observer.budget is profile.budget
    assert service.reserve_bytes == profile.budget.min_free_disk_bytes
    assert profile.evidence['packet_copy']['verification_status'] == 'unknown'
    assert not runtime.running and material is None

    monkeypatch.setattr(main, 'device_runtime', runtime)
    identities['packet_copy'] = 'b' * 64
    assert main._device_resource_profile() is profile
    assert main._device_operation_budget('packet_copy') is service.budget
