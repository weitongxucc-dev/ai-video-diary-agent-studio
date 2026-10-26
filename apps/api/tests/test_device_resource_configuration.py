"""Device policy selection and consumers, without native work or user state."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.device_resource_configuration import resolve_device_resources, _hash_file
from app.device_resource_profile import DeviceProfileUnavailable
from test_device_resource_profile import DEVICE, BACKENDS, profile_document, write_profile
from test_device_runtime_wiring import _CALIBRATED_ENV


def test_normal_selection_ignores_experiment_limits_and_reloads_same_path(tmp_path):
    document = profile_document()
    path = write_profile(tmp_path, document)
    settings = Settings(device_resource_profile_path=str(path))
    def resolve():
        return resolve_device_resources(settings, _CALIBRATED_ENV, device=DEVICE, backend_identities=BACKENDS)
    first = resolve()
    assert first.budget.cpu_render.max_clip_count == 6
    document['revision'] += 1
    document['budget']['cpu_render']['max_clip_count'] = 20
    write_profile(tmp_path, document)
    second = resolve()
    assert second.revision == first.revision + 1
    assert second.digest != first.digest and second.budget.cpu_render.max_clip_count == 20
    assert first.budget.cpu_render.max_clip_count == 6


def test_explicit_experiment_requires_identity_and_carries_unverified_source():
    settings = Settings(device_resource_mode='experiment')
    with pytest.raises(DeviceProfileUnavailable, match='实验身份'):
        resolve_device_resources(settings, _CALIBRATED_ENV)
    selected = resolve_device_resources(replace(settings, device_resource_experiment_id='isolated-fixture'), _CALIBRATED_ENV)
    assert selected.source_kind == 'explicit_experiment'
    assert selected.budget.cpu_render.max_clip_count == 60
    assert selected.budget.quality is None
    assert all(item['verification_status'] == 'unknown' for item in selected.evidence.values())
    with pytest.raises(DeviceProfileUnavailable, match='模式无效'):
        resolve_device_resources(replace(settings, device_resource_mode='automatic-env'), _CALIBRATED_ENV)


def test_missing_normal_profile_gives_preparation_action_without_env_fallback():
    with pytest.raises(DeviceProfileUnavailable, match='准备'):
        resolve_device_resources(Settings(), _CALIBRATED_ENV)


def test_backend_hash_cache_invalidates_file_replacement(tmp_path):
    binary = tmp_path / 'fake-tool'
    binary.write_bytes(b'first')
    first = _hash_file(binary)
    assert _hash_file(binary) == first
    binary.write_bytes(b'other')
    assert _hash_file(binary) != first


def test_main_selected_revision_is_pinned_until_runtime_released(tmp_path, monkeypatch):
    from app import main
    path = write_profile(tmp_path, profile_document())
    selected = resolve_device_resources(Settings(device_resource_profile_path=str(path)), {}, device=DEVICE, backend_identities=BACKENDS)
    monkeypatch.setattr(main, 'device_runtime', SimpleNamespace(resource_profile=selected))
    monkeypatch.setattr(main, 'resolve_device_resources', lambda *a, **kw: pytest.fail('active revision must stay pinned'))
    assert main._device_host_budget() is selected.budget
    assert main._device_operation_budget('quality') is None
    monkeypatch.setattr(main, 'device_runtime', None)
    next_profile = replace(selected, revision=selected.revision + 1)
    monkeypatch.setattr(main, 'resolve_device_resources', lambda *a, **kw: next_profile)
    assert main._device_resource_profile().revision == next_profile.revision


def test_missing_profile_keeps_metadata_factories_available_and_explicit_none_stays_none(monkeypatch):
    from app import main
    from app.member_permits import HostBudgetUncalibrated
    def unavailable():
        raise HostBudgetUncalibrated('请准备设备档')
    monkeypatch.setattr(main, '_device_host_budget', unavailable)
    monkeypatch.setattr(main, 'device_runtime', None)
    assert main._device_operation_budget('cpu_audio') is None
    case = main._quality_inspection_use_case()
    assert case.budget is None and '准备' in case.budget_error
    monkeypatch.setattr(main, '_device_operation_budget', lambda name: pytest.fail('explicit None must not fall back'))
    assert main._quality_inspection_use_case(budget=None).budget is None
    assert main._cover_use_case(budget=None).budget is None
    assert main._native_task_operations_case(budget=None).budget is None


def test_normal_profile_build_uses_one_selected_budget_without_starting_runtime(tmp_path, monkeypatch):
    from app import main
    from app.production import ProductionRepository
    path = write_profile(tmp_path, profile_document())
    settings = replace(main.settings, device_resource_mode='normal', device_resource_profile_path=str(path))
    selected = resolve_device_resources(settings, _CALIBRATED_ENV, device=DEVICE, backend_identities=BACKENDS)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'device_runtime', None)
    monkeypatch.setattr(main, 'resolve_device_resources', lambda *a, **kw: selected)
    monkeypatch.setattr(main, 'material_execution_mode', 'legacy')
    repository = ProductionRepository(tmp_path / 'isolated.sqlite', tmp_path / 'library')
    runtime, material = main._build_device_components(repository=repository)
    assert not runtime.running and material is None
    assert runtime.resource_profile is selected
    assert runtime.host_observer.budget is selected.budget
    assert runtime.host_observer.budget.cpu_render.max_clip_count == 6
    assert runtime.host_observer.budget.quality is None


def test_model_identity_invalidates_changed_weights_without_hashing_weight_bytes(tmp_path, monkeypatch):
    import json
    import app.device_resource_configuration as configuration
    python = isolated_model_runtime(tmp_path)
    weights, worker = (tmp_path / name for name in ('weights.bin', 'worker.py'))
    for item in (weights, worker, python):
        item.write_bytes(b'first')
    (tmp_path / 'manifest.json').write_text(json.dumps({'files': [{'path': weights.name}]}))
    (tmp_path / 'qwen3_text_embedding.py').write_text('helper')
    status = SimpleNamespace(ready=True, model_path=str(tmp_path), worker_path=str(worker), runtime_python_path=str(python))
    catalog = SimpleNamespace(status=lambda spec: status)
    hashed = []
    original = configuration._hash_file
    def hash_file(path):
        hashed.append(str(path))
        return original(path)
    monkeypatch.setattr(configuration, '_hash_file', hash_file)
    first = configuration.backend_identity('narration', None, settings=Settings(), model_catalog=catalog)
    weights.write_bytes(b'other')
    second = configuration.backend_identity('narration', None, settings=Settings(), model_catalog=catalog)
    assert first and second and first != second
    assert str(weights) not in hashed
    assert configuration.backend_identity('media_script', None, settings=Settings()) is None
    assert configuration.backend_identity('presenter', None, settings=Settings()) is None


def isolated_model_runtime(tmp_path):
    runtime = tmp_path / 'runtime'
    python = runtime / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.write_bytes(b'isolated-python-fixture')
    (runtime / 'pyvenv.cfg').write_text('include-system-site-packages = false\n')
    site = runtime / 'lib' / 'python3.12' / 'site-packages'
    for package in ('mlx', 'mlx_audio', 'numpy'):
        code = site / package / '__init__.py'
        code.parent.mkdir(parents=True)
        code.write_text('fixture')
        record = site / (package + '-1.0.dist-info') / 'RECORD'
        record.parent.mkdir()
        record.write_text(package + '/__init__.py,,\n')
    return python


def test_narration_identity_binds_helper_and_actual_runtime_files(tmp_path):
    from app.device_resource_configuration import backend_identity
    import json
    python = isolated_model_runtime(tmp_path)
    worker, helper = tmp_path / 'worker.py', tmp_path / 'qwen3_text_embedding.py'
    for path in (worker, helper):
        path.write_text('fixture')
    (tmp_path / 'manifest.json').write_text(json.dumps({'files': []}))
    status = SimpleNamespace(ready=True, model_path=str(tmp_path), worker_path=str(worker), runtime_python_path=str(python))
    catalog = SimpleNamespace(status=lambda spec: status)
    identity = lambda: backend_identity('narration', None, settings=Settings(), model_catalog=catalog)
    first = identity()
    helper.write_text('changed-helper')
    second = identity()
    assert first and second and first != second
    (python.parent.parent / 'lib/python3.12/site-packages/mlx/__init__.py').write_text('changed-installed-code')
    third = identity()
    assert third and third != second
    (python.parent.parent / 'installed-requirements.json').write_text('{"requirements":["mlx-audio==0.5.0"]}')
    (python.parent.parent / 'lib/python3.12/site-packages/mlx-1.0.dist-info/RECORD').unlink()
    assert identity() is None  # Installer declaration cannot replace missing actual dependencies.


def test_narration_execution_facts_bind_provider_and_generator_owned_task_paths(tmp_path):
    import json
    from app.device_resource_configuration import narration_execution_facts, backend_identity
    python = isolated_model_runtime(tmp_path)
    model = tmp_path / 'installed-model'
    model.mkdir()
    (model / 'manifest.json').write_text(json.dumps({'files': []}))
    worker = tmp_path / 'voice-worker.py'
    worker.write_text('worker-fixture')
    helper = tmp_path / 'qwen3_text_embedding.py'
    helper.write_text('helper-fixture')
    status = SimpleNamespace(ready=True, model_path=model, worker_path=worker, runtime_python_path=python)
    catalog = SimpleNamespace(status=lambda spec: status)
    provider = SimpleNamespace(model_path=model, worker_path=worker, runtime_python_path=python,
        temporary_root=tmp_path / 'actual-temporary', cache_root=tmp_path / 'actual-model-cache',
        generate_granted=lambda **kwargs: pytest.fail('execution facts must not generate audio'))
    observed_tasks = []
    output = tmp_path / 'generator-owned-output' / 'current-task'
    def task_directory(*, workspace_id, task_id):
        observed_tasks.append((workspace_id, task_id))
        return output
    generator = SimpleNamespace(provider=provider, task_output_directory=task_directory)
    settings = Settings()
    def facts():
        return narration_execution_facts(settings=settings, model_catalog=catalog, generator=generator,
            database_path=tmp_path / 'database-volume' / 'studio.sqlite',
            workspace_id='current-workspace', task_id='current-task')
    result = facts()
    assert observed_tasks == [('current-workspace', 'current-task')]
    assert result == {'backend_identity': backend_identity('narration', None, settings=settings, model_catalog=catalog),
        'write_paths': {'database': str(tmp_path / 'database-volume'),
            'temporary': str(provider.temporary_root), 'model_cache': str(provider.cache_root), 'output': str(output)}}
    assert not output.exists() and not provider.temporary_root.exists()
    helper.write_text('changed-helper')
    assert facts()['backend_identity'] != result['backend_identity']
    provider.worker_path = tmp_path / 'unmatched-worker.py'
    provider.worker_path.write_text('different-worker')
    with pytest.raises(DeviceProfileUnavailable, match='不一致'):
        facts()
    provider.worker_path = worker
    other_python = tmp_path / 'other-runtime' / 'bin' / 'python'
    other_python.parent.mkdir(parents=True)
    other_python.symlink_to(python)
    assert other_python.resolve() == python.resolve()
    provider.runtime_python_path = other_python
    with pytest.raises(DeviceProfileUnavailable, match='不一致'):
        facts()  # The same interpreter binary does not bind the same venv.
