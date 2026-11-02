"""Isolated owner contracts; fake grant command results are not model measurements."""
from dataclasses import asdict, replace
import json
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

from app.device_resource_profile import DeviceProfileUnavailable
from app.narration_resource_preparation import NarrationResourcePreparation, NarrationPreparationShape
from app.profiles.contracts import VoiceProfile
from app.profile_reference_media import ProfileReferenceInput
from app.voice_clone import VoiceCloneResult
from app.granted_subprocess import GrantedCommandResult, ObservedCommandResources
from app.device_runtime import build_device_runtime
from app.host_resource_probes import HostResourceProbe
from test_device_resource_preparation import preparation
from test_device_resource_profile import profile_document
from test_device_resource_configuration import isolated_model_runtime
from test_device_scheduler import Resources, Processes


def fixture(tmp_path, monkeypatch):
    repository, actor, access, cpu, paths = preparation(tmp_path)
    document = profile_document()
    document['device'] = asdict(cpu.device)
    cpu.profile_path.parent.mkdir(parents=True, exist_ok=True)
    cpu.profile_path.write_text(json.dumps(document))
    python = isolated_model_runtime(tmp_path)
    model = tmp_path / 'models' / 'qwen'
    model.mkdir(parents=True)
    (model / 'manifest.json').write_text(json.dumps({'files': []}))
    worker = tmp_path / 'worker.py'
    worker.write_text('fake-model-worker')
    (tmp_path / 'qwen3_text_embedding.py').write_text('fake-helper')
    status = SimpleNamespace(ready=True, model_path=str(model), worker_path=str(worker), runtime_python_path=str(python))
    reference_path = tmp_path / 'reference.wav'
    with wave.open(str(reference_path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\0\0' * 16000)
    voice = VoiceProfile('voice', 'local', 'v1', 'zh', str(reference_path), '参考声音')
    reference = ProfileReferenceInput.from_source(source_path=reference_path, workspace_id='local',
        profile_id='voice', profile_version='v1', asset_id=None, segment_id=None, source_type='audio',
        kind='voice_audio', start_seconds=None, end_seconds=None, source_duration_seconds=1)
    monkeypatch.setattr(repository.profile_records, 'get_versioned_voice_profile', lambda workspace, identity, version: voice)
    def resolve(selected, **kwargs):
        assert kwargs['viewer_user_id'] == actor.member_id and kwargs['for_generation'] is True
        return reference
    monkeypatch.setattr(repository, 'profile_reference_input', resolve)
    provider = SimpleNamespace(ready=True, message='ready', supported_languages=('zh', 'en', 'zh-en'),
        supports_pronunciation_dictionary=True, runtime_python_path=python, worker_path=worker, model_path=model,
        temporary_root=paths['temporary'], cache_root=model.parent / '.cache')
    def generate(**kwargs):
        kwargs['grant_context'].run([str(python), str(worker)])
        output = Path(kwargs['output_path'])
        with wave.open(str(output), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(24000)
            stream.writeframes(b'\0\0' * 24000)
        return VoiceCloneResult(str(output), 1, 24000)
    provider.generate_granted = generate
    service = NarrationResourcePreparation(repository=repository, model_catalog=SimpleNamespace(status=lambda spec: status),
        provider=provider, profile_path=cpu.profile_path, settings=cpu.settings, device=cpu.device,
        write_paths=paths, verify_identity=access.verify_identity)
    monkeypatch.setattr('app.narration_resource_preparation.sample_host_memory',
        lambda: SimpleNamespace(pressure='normal', available_bytes=4 * 1024**3))
    return repository, actor, access, service, voice


def test_model_preparation_binds_real_reference_and_dirs_without_publishing_or_running(tmp_path, monkeypatch):
    repository, actor, access, service, voice = fixture(tmp_path, monkeypatch)
    shape = NarrationPreparationShape('voice', 'v1', '一次样本。', 'zh', 2)
    original = service.profile_path.read_bytes()
    plan_id, budget = service.prepare(shape, actor, request_id='request')
    assert budget.narration.input_scope == 'single_call_preparation_v1'
    assert budget.narration.preparation_receipt_sha256 is None
    plan = service._plan(plan_id)
    assert plan['inputs']['reference_sha256'] and 'model_cache' in plan['actual_write_paths']
    assert service.prepare(shape, actor, request_id='request')[0] == plan_id
    with pytest.raises(ValueError, match='输入已变化'):
        service.prepare(replace(shape, text='changed'), actor, request_id='request')
    assert service.profile_path.read_bytes() == original
    monkeypatch.setattr(repository.profile_records, 'get_versioned_voice_profile', lambda *args: replace(voice, use_state='excluded'))
    with pytest.raises(PermissionError):
        service.prepare(shape, actor, request_id='revoked')


def test_model_owner_publishes_only_after_original_scheduler_checkpoint_and_invalidates_dependency(tmp_path, monkeypatch):
    repository, actor, access, service, voice = fixture(tmp_path, monkeypatch)
    plan_id, budget = service.prepare(NarrationPreparationShape('voice', 'v1', '一次样本。', 'zh', 2), actor, request_id='model')
    runtime = build_device_runtime(database_path=repository.database_path, device_lock_path=tmp_path / 'lock',
        device_access=access, registrations={'device_preparation': service.registration()},
        resources=Resources(), processes=Processes())
    request = runtime.scheduler.submit(service.intent(plan_id, actor), actor)
    with pytest.raises(DeviceProfileUnavailable, match='完成回执'):
        service.finalize(database_path=repository.database_path, request_id=request.id, actor=actor)
    monkeypatch.setattr('app.granted_subprocess.run_granted_command', lambda *args, **kwargs:
        GrantedCommandResult(0, b'', b'', ObservedCommandResources(1024**2, 2, True, True)))
    try:
        grant = runtime.scheduler.pick_and_grant()
        assert grant is not None
        service.execute(grant, runtime.scheduler)
        assert service.status(database_path=repository.database_path, request_id=request.id, actor=actor)['state'] == 'done'
        result = service.finalize(database_path=repository.database_path, request_id=request.id, actor=actor)
        assert result.budget.narration.input_scope == 'single_call_prepared_v1'
        assert result.budget.narration.preparation_receipt_sha256
        assert result.evidence['narration']['verification_status'] == 'unknown'
        assert service.finalize(database_path=repository.database_path, request_id=request.id, actor=actor).digest == result.digest
        (tmp_path / 'qwen3_text_embedding.py').write_text('different-helper')
        assert service._load().budget.narration is None
        with pytest.raises(ValueError, match='已变化'):
            service.finalize(database_path=repository.database_path, request_id=request.id, actor=actor)
    finally:
        assert runtime.stop(2)


def test_preparation_candidate_cannot_admit_production_owner_or_claim_prepared_without_receipt(tmp_path, monkeypatch):
    repository, actor, access, service, voice = fixture(tmp_path, monkeypatch)
    plan_id, budget = service.prepare(NarrationPreparationShape('voice', 'v1', '样本', 'zh', 2), actor, request_id='owner')
    probe = HostResourceProbe(repository.database_path, disk_paths=tuple(service.write_paths.values()), budget=budget)
    decision = probe.admit(SimpleNamespace(resources=service._resources(service._plan(plan_id)),
        owner_type='creation_job', unit_key='narration:segment:v1:one'))
    assert decision.reason == 'narration_preparation_owner_invalid' and decision.verdict is False
    with pytest.raises(ValueError, match='original receipt'):
        replace(budget.narration, input_scope='single_call_prepared_v1')


def test_changed_source_and_unbound_provider_temp_paths_cannot_use_preparation(tmp_path, monkeypatch):
    repository, actor, access, service, voice = fixture(tmp_path, monkeypatch)
    service.provider.temporary_root = tmp_path / 'other-volume'
    with pytest.raises(DeviceProfileUnavailable, match='临时'):
        service.prepare(NarrationPreparationShape('voice', 'v1', '样本', 'zh', 2), actor, request_id='paths')


def test_preparation_rejects_same_interpreter_binary_from_another_venv(tmp_path, monkeypatch):
    repository, actor, access, service, voice = fixture(tmp_path, monkeypatch)
    original = service.provider.runtime_python_path
    other = tmp_path / 'another-runtime' / 'bin' / 'python'
    other.parent.mkdir(parents=True)
    other.symlink_to(original)
    assert other.resolve() == original.resolve()
    service.provider.runtime_python_path = other
    with pytest.raises(DeviceProfileUnavailable, match='不一致'):
        service.prepare(NarrationPreparationShape('voice', 'v1', '样本', 'zh', 2), actor, request_id='venv')
    assert not list((service.root / 'plans').glob('*.json'))


def test_normal_profile_cannot_promote_declared_candidate_or_fabricated_receipt_label(tmp_path, monkeypatch):
    repository, actor, access, service, voice = fixture(tmp_path, monkeypatch)
    plan_id, budget = service.prepare(NarrationPreparationShape('voice', 'v1', '样本', 'zh', 2), actor, request_id='profile')
    document = json.loads(service.profile_path.read_bytes())
    document['budget']['narration'] = asdict(budget.narration)
    document['backends']['narration'] = service._identity()
    service.profile_path.write_text(json.dumps(document))
    assert service._load().unavailable['narration'] == 'model_preparation_not_completed'
    document['budget']['narration'].update(input_scope='single_call_prepared_v1', preparation_receipt_sha256='a'*64)
    service.profile_path.write_text(json.dumps(document))
    result = service._load()
    assert result.budget.narration is None
    assert result.unavailable['narration'] == 'model_preparation_receipt_missing'


def test_prepared_scope_binds_actual_inputs_and_checks_separate_model_cache_volume(tmp_path, monkeypatch):
    from tests.host_memory_boundary import IsolatedMemoryMonitor
    from app import device_resource_volumes as volumes
    from app.host_resource_probes import narration_scope_basis
    from app.narration_resource_preparation import narration_preparation_shape_digest
    import psutil
    repository, actor, access, service, voice = fixture(tmp_path, monkeypatch)
    plan_id, bootstrap = service.prepare(NarrationPreparationShape('voice', 'v1', '样本', 'zh', 2), actor, request_id='volumes')
    plan = service._plan(plan_id)
    prepared = replace(bootstrap.narration, input_scope='single_call_prepared_v1', preparation_receipt_sha256='a'*64)
    resources = {**service._resources(plan), 'input_scope': prepared.input_scope,
        'budget_basis': narration_scope_basis(prepared.input_scope), 'preparation_receipt_sha256': 'a'*64}
    budget = replace(bootstrap, narration=prepared, max_disk_io_bytes_per_second=None)
    probe = HostResourceProbe(repository.database_path, disk_paths=tuple(service.write_paths.values()),
        budget=budget, memory_monitor=IsolatedMemoryMonitor())
    monkeypatch.setattr(psutil, 'disk_io_counters', lambda **kw: None)
    monkeypatch.setattr(psutil, 'cpu_percent', lambda **kw: 0)
    paths, reservations = plan['actual_write_paths'], plan['write_reservations']
    ids = {path: index for index, path in enumerate(paths.values())}
    free = {paths[role]: budget.min_free_disk_bytes + amount for role, amount in reservations.items()}
    monkeypatch.setattr(volumes, 'existing_volume', lambda path: (ids[str(path)], Path(path)))
    monkeypatch.setattr(psutil, 'disk_usage', lambda path: SimpleNamespace(free=free[str(path)]))
    decision = probe._sample(resources)
    assert decision.verdict is True, decision.reason
    free[paths['model_cache']] -= 1
    assert probe._sample(resources).verdict is False
    assert probe._sample({**resources, 'preparation_shape_digest': 'b'*64}).reason == 'narration_preparation_binding_changed'
    inputs = plan['inputs']
    changed = narration_preparation_shape_digest(voice_identity=inputs['voice'], reference=inputs['reference'],
        reference_sha256=inputs['reference_sha256'], reference_text='其他参考文字', language=inputs['language'])
    assert changed != inputs['shape_digest']


def test_bilingual_preparation_uses_original_pronunciation_versions_in_digest_and_granted_call(tmp_path, monkeypatch):
    from app.profiles.contracts import PronunciationEntry
    from app.narration_resource_preparation import narration_preparation_shape_digest
    repository, actor, access, service, voice = fixture(tmp_path, monkeypatch)
    voice = replace(voice, language='zh-en')
    monkeypatch.setattr(repository.profile_records, 'get_versioned_voice_profile', lambda *args: voice)
    entry = PronunciationEntry('term', actor.workspace_id, '曜核', 'zh', 'yao he', 'task', 'task', 'v1')
    repository.profile_records.save_pronunciation_entry(entry)
    monkeypatch.setattr(repository.task_records, 'get_creation_task', lambda workspace, task: SimpleNamespace(
        visibility='private', owner_user_id=actor.user_id or actor.member_id))
    shape = NarrationPreparationShape('voice', 'v1', '曜核样本', 'zh', 2,
        pronunciation_references=({'id': 'term', 'version': 'v1'},), task_id='task')
    plan_id, budget = service.prepare(shape, actor, request_id='words')
    assert service.prepare(shape, actor, request_id='words') == (plan_id, budget)
    plan = service._plan(plan_id)
    inputs = plan['inputs']
    assert inputs['pronunciation_dictionary'] == [entry.task_input()]
    assert budget.narration.preparation_shape_digest == narration_preparation_shape_digest(
        voice_identity=inputs['voice'], reference=inputs['reference'], reference_sha256=inputs['reference_sha256'],
        reference_text=voice.reference_text, language='zh', pronunciation_dictionary=(entry.task_input(),))
    calls = []
    generate = service.provider.generate_granted
    def record(**kwargs):
        calls.append(kwargs)
        return generate(**kwargs)
    service.provider.generate_granted = record
    runtime = build_device_runtime(database_path=repository.database_path, device_lock_path=tmp_path / 'lock',
        device_access=access, registrations={'device_preparation': service.registration()},
        resources=Resources(), processes=Processes())
    request = runtime.scheduler.submit(service.intent(plan_id, actor), actor)
    monkeypatch.setattr('app.granted_subprocess.run_granted_command', lambda *args, **kwargs:
        GrantedCommandResult(0, b'', b'', ObservedCommandResources(1024**2, 2, True, True)))
    try:
        grant = runtime.scheduler.pick_and_grant()
        assert grant is not None
        service.execute(grant, runtime.scheduler)
        assert service.status(database_path=repository.database_path, request_id=request.id, actor=actor)['state'] == 'done'
        assert len(calls) == 1 and calls[0]['language'] == 'zh'
        assert calls[0]['pronunciation_dictionary'] == (entry.task_input(),)
        service.finalize(database_path=repository.database_path, request_id=request.id, actor=actor)
        monkeypatch.setattr(repository.profile_records, 'get_pronunciation_entry', lambda *args: replace(entry, target_pronunciation='changed'))
        with pytest.raises(ValueError, match='变化'):
            service._recheck(plan, actor)
    finally:
        assert runtime.stop(2)


def test_preparation_pronunciation_scope_visibility_and_provider_contract_are_enforced(tmp_path, monkeypatch):
    from app.profiles.contracts import PronunciationEntry
    repository, actor, access, service, voice = fixture(tmp_path, monkeypatch)
    entry = PronunciationEntry('term', actor.workspace_id, '曜核', 'zh', 'yao he', 'task', 'task', 'v1')
    repository.profile_records.save_pronunciation_entry(entry)
    shape = NarrationPreparationShape('voice', 'v1', '样本', 'zh', 2,
        pronunciation_references=({'id': entry.id, 'version': entry.version},))
    with pytest.raises(PermissionError, match='范围'):
        service.prepare(shape, actor, request_id='missing-task')
    monkeypatch.setattr(repository.task_records, 'get_creation_task', lambda *args: SimpleNamespace(
        visibility='private', owner_user_id='someone-else'))
    with pytest.raises(PermissionError, match='当前成员'):
        service.prepare(replace(shape, task_id='task'), actor, request_id='private-task')
    monkeypatch.setattr(repository.task_records, 'get_creation_task', lambda *args: SimpleNamespace(
        visibility='private', owner_user_id=actor.user_id or actor.member_id))
    with pytest.raises(PermissionError, match='范围'):
        service.prepare(replace(shape, task_id='other-task'), actor, request_id='wrong-task')
    service.provider.supports_pronunciation_dictionary = False
    with pytest.raises(DeviceProfileUnavailable, match='词典'):
        service.prepare(replace(shape, task_id='task'), actor, request_id='unsupported')
    with pytest.raises(PermissionError):
        service.prepare(replace(shape, language='en', pronunciation_references=()), actor, request_id='wrong-language')
    with pytest.raises(ValueError, match='原条目'):
        replace(shape, pronunciation_references=({'id': entry.id, 'version': 'v1', 'term': 'forged'},))
    assert not list((service.root / 'plans').glob('*.json'))


def test_empty_pronunciation_defaults_preserve_original_preparation_request_binding(tmp_path, monkeypatch):
    repository, actor, access, service, voice = fixture(tmp_path, monkeypatch)
    shape = NarrationPreparationShape('voice', 'v1', '样本', 'zh', 2)
    plan_id, budget = service.prepare(shape, actor, request_id='original')
    plan = service._plan(plan_id)
    original_fields = {'voice_profile_id', 'voice_version', 'text', 'language', 'output_duration_seconds'}
    assert set(plan['shape']) == original_fields
    assert set(plan['request_binding']['shape']) == original_fields
    original = (service.root / 'plans' / (plan_id + '.json')).read_bytes()
    assert service.prepare(replace(shape, pronunciation_references=(), task_id=None),
        actor, request_id='original') == (plan_id, budget)
    assert (service.root / 'plans' / (plan_id + '.json')).read_bytes() == original
