from dataclasses import replace
import json
from pathlib import Path
import platform
import shutil
import sys
import time
from uuid import uuid4

import psutil
import pytest

from app.config import Settings
from app.creation_execution import CreationExecutionActor
from app.device_resource_preparation import DeviceResourcePreparation, CpuPreparationShape
from app.device_resource_profile import DeviceProfileIdentity, DeviceProfileUnavailable
from app.device_runtime import build_device_runtime
from app.host_resource_probes import HostResourceProbe, HostProcessProbe
from app.local_members import LocalMemberService
from app.member_permits import MemberDeviceAccess
from app.production import ProductionRepository


def preparation(tmp_path):
    repository = ProductionRepository(tmp_path / 'db' / 'production.sqlite', tmp_path / 'library')
    members = LocalMemberService(repository.database_path)
    member, _ = members.join(members.create_invite_code(), 'CPU preparation fixture')
    actor = CreationExecutionActor('local', member.member_id)
    access = MemberDeviceAccess(members)
    owner = tmp_path / 'device'
    settings = Settings(device_resource_profile_path=str(owner / 'device-resource-profile.json'))
    device = DeviceProfileIdentity('fixture-device', sys.platform, platform.machine(), psutil.virtual_memory().total)
    paths = {'database': repository.database_path.parent, 'output': tmp_path / 'output', 'temporary': tmp_path / 'temporary'}
    service = DeviceResourcePreparation(profile_path=settings.device_resource_profile_path, device=device,
        settings=settings, write_paths=paths, verify_identity=access.verify_identity)
    return repository, actor, access, service, paths


@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='macOS CPU preparation backend unavailable')
def test_real_granted_cpu_preparation_publishes_bound_profile_and_can_finalize_without_rerun(tmp_path):
    repository, actor, access, service, paths = preparation(tmp_path)
    shape = CpuPreparationShape(64, 64, 24, 1.0, 2, 2, 8000, 1)
    plan_id, budget = service.prepare(shape, actor, request_id=str(uuid4()))
    assert budget.max_disk_io_bytes_per_second is None
    assert not service.profile_path.exists()
    resources = HostResourceProbe(repository.database_path, disk_paths=paths, budget=budget)
    runtime = build_device_runtime(database_path=repository.database_path,
        device_lock_path=service.profile_path.parent / 'device-permit.lock', device_access=access,
        registrations={'device_preparation': service.registration()}, resources=resources,
        processes=HostProcessProbe(), host_observer=resources)
    request = runtime.scheduler.submit(service.intent(plan_id, actor), actor)
    try:
        with pytest.raises(DeviceProfileUnavailable, match='完成回执'):
            service.finalize(database_path=repository.database_path, request_id=request.id, actor=actor)
        runtime.start()
        deadline = time.monotonic() + service.policy.timeout_seconds + 5
        while time.monotonic() < deadline:
            with repository._connect() as connection:
                row = connection.execute('SELECT state,wait_reason FROM device_work_requests WHERE id=?', (request.id,)).fetchone()
            if row['state'] in {'done', 'failed'} or runtime.last_error is not None:
                break
            time.sleep(.1)
        assert row['state'] == 'done', (dict(row), runtime.last_error)
        result = service.finalize(database_path=repository.database_path, request_id=request.id, actor=actor)
        assert result.revision == 1 and result.budget.cpu_render.max_clip_count == 2
        assert result.budget.cpu_audio.max_input_count == 2
        assert result.budget.cpu_audio.max_output_bytes >= 32000
        assert result.budget.narration is None and result.budget.presenter is None
        assert result.budget.max_disk_io_bytes_per_second is None
        assert result.evidence['cpu_render']['status'] == 'recorded'
        assert result.evidence['cpu_render']['verification_status'] == 'unknown'
        again = service.finalize(database_path=repository.database_path, request_id=request.id, actor=actor)
        assert again.digest == result.digest and again.revision == 1
        assert list(Path(paths['temporary']).iterdir()) == []
        with repository._connect() as connection:
            assert connection.execute('SELECT count(*) FROM device_work_requests').fetchone()[0] == 1
        # A modified receipt cannot be adopted just because a caller supplies facts.
        receipt_path = next((service.root / 'receipts').glob('*.json'))
        receipt_path.write_text('{}')
        original_profile = service.profile_path.read_bytes()
        with pytest.raises(DeviceProfileUnavailable, match='检查点不匹配'):
            service.finalize(database_path=repository.database_path, request_id=request.id, actor=actor)
        assert service.profile_path.read_bytes() == original_profile
    finally:
        assert runtime.stop(2)


def test_normal_device_lock_is_independent_of_workspace_database(tmp_path):
    from app.bootstrap import device_lock_path
    settings = Settings(device_resource_profile_path=str(tmp_path / 'device' / 'device-resource-profile.json'))
    assert device_lock_path(settings=settings, database_path=tmp_path / 'a.sqlite') == device_lock_path(settings=settings, database_path=tmp_path / 'b.sqlite')
    another = Settings(device_resource_profile_path=str(tmp_path / 'other-profile' / 'profile.json'))
    assert device_lock_path(settings=settings, database_path=tmp_path / 'a.sqlite') == device_lock_path(settings=another, database_path=tmp_path / 'b.sqlite')


def test_prepare_normal_pressure_uses_available_headroom_not_fraction_of_physical_memory(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import app.device_resource_preparation as preparation_module
    repository, actor, access, service, paths = preparation(tmp_path)
    available, host_rss = 2 * 1024**3, 100 * 1024**2
    monkeypatch.setattr(preparation_module, 'sample_host_memory', lambda: SimpleNamespace(pressure='normal', available_bytes=available))
    monkeypatch.setattr(preparation_module.psutil, 'Process', lambda: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=host_rss)))
    plan_id, budget = service.prepare(CpuPreparationShape(64, 64, 24, 1, 2, 2, 8000, 1), actor, request_id=str(uuid4()))
    assert budget.min_available_memory_bytes == host_rss
    assert budget.cpu_render.additional_memory_bytes == (available - host_rss) // 2
    assert service._plan(plan_id)['host_observation']['scope'] == 'observation_at_prepare_not_future_capacity'
    assert not service.profile_path.exists()


def test_prepare_critical_pressure_does_not_start_work_or_publish_profile(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import app.device_resource_preparation as preparation_module
    repository, actor, access, service, paths = preparation(tmp_path)
    monkeypatch.setattr(preparation_module, 'sample_host_memory', lambda: SimpleNamespace(pressure='critical', available_bytes=12 * 1024**3))
    with pytest.raises(DeviceProfileUnavailable, match='内存压力过高'):
        service.prepare(CpuPreparationShape(64, 64, 24, 1, 2, 2, 8000, 1), actor, request_id=str(uuid4()))
    assert not service.profile_path.exists()
    assert list((service.root / 'plans').glob('*.json')) == []
    with repository._connect() as connection:
        assert connection.execute('SELECT count(*) FROM device_work_requests').fetchone()[0] == 0


def test_held_legacy_lock_or_uncertain_receipt_prevents_lock_root_change(tmp_path):
    import fcntl
    from app.device_paths import require_legacy_locks_released
    path = tmp_path / 'old-device-permit.lock'
    with path.open('wb') as old:
        fcntl.flock(old, fcntl.LOCK_EX)
        with pytest.raises(RuntimeError, match='仍被持有'):
            require_legacy_locks_released([path])
        fcntl.flock(old, fcntl.LOCK_UN)
    ledger = Path(str(path) + '.state')
    ledger.write_text(json.dumps({'state': 'held'}))
    with pytest.raises(RuntimeError, match='尚未确认释放'):
        require_legacy_locks_released([path])
    ledger.write_text(json.dumps({'state': 'free'}))
    require_legacy_locks_released([path])


def _synthetic_publication(service, plan_id):
    """Writer failure fixture only; never accepted by public finalize without DB proof."""
    plan = service._plan(plan_id)
    return plan, {'observed_resources': [{'peak_rss_bytes': 1024**2, 'peak_threads': 2,
        'rss_sample_complete': True, 'threads_sample_complete': True}],
        'elapsed_seconds': 1.0, 'request_id': 'writer-fixture', 'generation': 1,
        'input_digest': plan['input_digest'], 'media': {'audio': {'bytes': 32114}, 'fixture': 'no-media-executed'}}


def test_writer_rejects_stale_revision_and_failure_preserves_existing_profile(tmp_path, monkeypatch):
    import app.device_resource_preparation as module
    repository, actor, access, service, paths = preparation(tmp_path)
    from test_device_resource_profile import profile_document
    original = profile_document()
    original['device'] = __import__('dataclasses').asdict(service.device)
    service.profile_path.parent.mkdir(parents=True, exist_ok=True)
    service.profile_path.write_text(json.dumps(original))
    plan_id, _ = service.prepare(CpuPreparationShape(64, 64, 24, 1, 2, 2, 8000, 1), actor, request_id=str(uuid4()))
    plan, receipt = _synthetic_publication(service, plan_id)
    changed = dict(original, revision=original['revision'] + 1)
    service.profile_path.write_text(json.dumps(changed))
    with pytest.raises(DeviceProfileUnavailable, match='不能覆盖更新版本'):
        service._publish(plan, receipt, plan['backends'])
    assert json.loads(service.profile_path.read_bytes()) == changed
    service.profile_path.write_text(json.dumps(original))
    before = service.profile_path.read_bytes()
    real_write = module._atomic_json
    def fail_candidate(path, document):
        if str(path).endswith('.candidate.json'):
            raise OSError('isolated-disk-write-failure')
        return real_write(path, document)
    monkeypatch.setattr(module, '_atomic_json', fail_candidate)
    with pytest.raises(OSError, match='isolated-disk-write-failure'):
        service._publish(plan, receipt, plan['backends'])
    assert service.profile_path.read_bytes() == before
    history = list((service.profile_path.parent / 'resource-profile-history').glob('*.json'))
    assert len(history) == 1 and json.loads(history[0].read_bytes()) == original


def test_twenty_inputs_are_prepared_as_unit_shape_not_rejected_by_cpu_core_count(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import app.device_resource_preparation as module
    repository, actor, access, service, paths = preparation(tmp_path)
    monkeypatch.setattr(module, 'sample_host_memory', lambda: SimpleNamespace(pressure='normal', available_bytes=2 * 1024**3))
    monkeypatch.setattr(module.psutil, 'Process', lambda: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=100 * 1024**2)))
    monkeypatch.setattr(module.psutil, 'cpu_count', lambda: 4)
    plan_id, budget = service.prepare(CpuPreparationShape(64, 64, 24, 1, 20, 20, 8000, 1), actor, request_id=str(uuid4()))
    assert budget.cpu_render.max_clip_count == 20
    assert budget.cpu_render.max_process_threads >= 20
    assert service._plan(plan_id)['shape']['audio_input_count'] == 20
    assert not service.profile_path.exists()  # Preparing an envelope is not measuring it.


def test_published_audio_budget_covers_measured_container_and_known_source_bytes(tmp_path):
    repository, actor, access, service, paths = preparation(tmp_path)
    plan_id, _ = service.prepare(CpuPreparationShape(64, 64, 24, 1, 2, 2, 8000, 1, source_bytes=1000000), actor, request_id=str(uuid4()))
    plan, receipt = _synthetic_publication(service, plan_id)
    result = service._publish(plan, receipt, plan['backends'])
    assert result.budget.cpu_audio.max_output_bytes == receipt['media']['audio']['bytes']
    assert result.budget.cpu_audio.max_input_bytes == 1000000
    assert result.budget.cpu_render.max_input_bytes == 1000000


def test_cpu_preparation_preserves_existing_host_policy_and_unrelated_capacity(tmp_path, monkeypatch):
    from dataclasses import asdict
    from types import SimpleNamespace
    import app.device_resource_preparation as module
    from test_device_resource_profile import profile_document
    repository, actor, access, service, paths = preparation(tmp_path)
    original = profile_document()
    original['device'] = asdict(service.device)
    original['budget'].update(sample_seconds=.2, max_swap_used_bytes=1234)
    service.profile_path.parent.mkdir(parents=True, exist_ok=True)
    service.profile_path.write_text(json.dumps(original))
    monkeypatch.setattr(module, 'sample_host_memory', lambda: SimpleNamespace(pressure='normal', available_bytes=4 * 1024**3))
    monkeypatch.setattr(module.psutil, 'Process', lambda: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=100 * 1024**2)))
    plan_id, bootstrap = service.prepare(CpuPreparationShape(64, 64, 24, 1, 2, 2, 8000, 1), actor, request_id='retain-host-policy')
    for name in ('max_cpu_percent', 'min_available_memory_bytes', 'min_free_disk_bytes',
                 'max_disk_io_bytes_per_second', 'max_swap_used_bytes', 'sample_seconds', 'max_unit_input_bytes'):
        assert getattr(bootstrap, name) == original['budget'][name]
    assert bootstrap.cpu_render.additional_memory_bytes == (4 * 1024**3 - original['budget']['min_available_memory_bytes']) // 2
    plan, receipt = _synthetic_publication(service, plan_id)
    service._publish(plan, receipt, plan['backends'])
    published = json.loads(service.profile_path.read_bytes())
    for name, value in original['budget'].items():
        if name != 'cpu_render':
            assert published['budget'][name] == value
    host_evidence = published['evidence']['host']
    record = json.loads((service.profile_path.parent / 'resource-profile-evidence' / (host_evidence['record_id'] + '.json')).read_bytes())
    assert record['input_range']['host_policy_source']['profile_digest'] == module._digest(original)
    assert record['input_range']['host_policy_source']['max_unit_input_bytes_scope'] == 'per_unit_input_capacity_not_cumulative_authorization'


def test_prepare_request_key_reuses_original_plan_without_remeasuring_and_rejects_changed_input(tmp_path, monkeypatch):
    import app.device_resource_preparation as module
    repository, actor, access, service, paths = preparation(tmp_path)
    shape = CpuPreparationShape(64, 64, 24, 1, 2, 2, 8000, 1)
    plan_id, budget = service.prepare(shape, actor, request_id='stable-request')
    monkeypatch.setattr(module, 'sample_host_memory', lambda: pytest.fail('replayed preparation must not measure again'))
    replay_id, replay_budget = service.prepare(shape, actor, request_id='stable-request')
    assert replay_id == plan_id and replay_budget == budget
    with pytest.raises(ValueError, match='输入已变化'):
        service.prepare(replace(shape, width_px=128), actor, request_id='stable-request')
    assert len(list((service.root / 'plans').glob('*.json'))) == 1


def test_preparation_keeps_account_actor_identity_during_admission_recheck(tmp_path):
    from types import SimpleNamespace
    repository, local_actor, access, service, paths = preparation(tmp_path)
    actor = CreationExecutionActor.account(str(uuid4()), str(uuid4()), str(uuid4()))
    observed = []
    def verify(candidate):
        observed.append(candidate)
        return 'ready' if candidate == actor else 'unauthorized'
    service.verify_identity = verify
    plan_id, budget = service.prepare(CpuPreparationShape(64, 64, 24, 1, 2, 2, 8000, 1), actor, request_id='cloud-request')
    plan = service._plan(plan_id)
    assert plan['actor_identity'] == actor.identity_payload()
    work = service.verify_submission(service.intent(plan_id, actor), actor)
    request = SimpleNamespace(**vars(work))
    assert service.check_current(request, None) == 'ready'
    assert all(item == actor for item in observed)


def test_plan_write_failure_retries_same_identity_without_duplicate_plan(tmp_path, monkeypatch):
    import app.device_resource_preparation as module
    repository, actor, access, service, paths = preparation(tmp_path)
    shape = CpuPreparationShape(64, 64, 24, 1, 2, 2, 8000, 1)
    original = module._atomic_json
    attempted = []
    def uncertain_write(path, payload):
        attempted.append(Path(path))
        original(path, payload)
        raise OSError('reply lost after durable write')
    monkeypatch.setattr(module, '_atomic_json', uncertain_write)
    with pytest.raises(OSError, match='reply lost'):
        service.prepare(shape, actor, request_id='uncertain-request')
    monkeypatch.setattr(module, 'sample_host_memory', lambda: pytest.fail('unknown write must be reconciled first'))
    plan_id, _ = service.prepare(shape, actor, request_id='uncertain-request')
    assert attempted == [service.root / 'plans' / f'{plan_id}.json']
    assert len(list((service.root / 'plans').glob('*.json'))) == 1


def test_status_is_read_only_after_service_recreation_and_rejects_other_owner_or_member(tmp_path):
    repository, actor, access, service, paths = preparation(tmp_path)
    plan_id, budget = service.prepare(CpuPreparationShape(64, 64, 24, 1, 2, 2, 8000, 1), actor, request_id='status-request')
    resources = HostResourceProbe(repository.database_path, disk_paths=paths, budget=budget)
    runtime = build_device_runtime(database_path=repository.database_path,
        device_lock_path=service.profile_path.parent / 'device-permit.lock', device_access=access,
        registrations={'device_preparation': service.registration()}, resources=resources, processes=HostProcessProbe())
    request = runtime.scheduler.submit(service.intent(plan_id, actor), actor)
    assert runtime.stop(0)
    recreated = DeviceResourcePreparation(profile_path=service.profile_path, device=service.device,
        settings=service.settings, write_paths=paths, verify_identity=access.verify_identity)
    result = recreated.status(database_path=repository.database_path, request_id=request.id, actor=actor)
    assert result == {'id': request.id, 'plan_id': plan_id, 'state': 'waiting', 'wait_reason': None}
    with pytest.raises(PermissionError):
        recreated.status(database_path=repository.database_path, request_id=request.id,
            actor=CreationExecutionActor('local', 'other-member'))
    with pytest.raises(KeyError):
        recreated.status(database_path=repository.database_path, request_id='missing-request', actor=actor)
    with repository._connect() as connection:
        assert connection.execute('SELECT count(*) FROM device_work_requests').fetchone()[0] == 1
        connection.execute("UPDATE device_work_requests SET owner_type='creation_job' WHERE id=?", (request.id,))
    with pytest.raises(PermissionError):
        recreated.status(database_path=repository.database_path, request_id=request.id, actor=actor)


def test_request_kind_uses_original_unit_and_rejects_other_identity_without_writes(tmp_path):
    import sqlite3
    from app.device_resource_preparation import request_kind
    path = tmp_path / 'requests.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE device_work_requests '
            '(id TEXT PRIMARY KEY, owner_type TEXT, workspace_id TEXT, member_id TEXT, unit_key TEXT)')
        connection.executemany('INSERT INTO device_work_requests VALUES (?,?,?,?,?)', [
            ('cpu', 'device_preparation', 'workspace', 'member', 'cpu-prepare:v1'),
            ('voice', 'device_preparation', 'workspace', 'member', 'narration-prepare:v1'),
            ('unknown', 'device_preparation', 'workspace', 'member', 'unknown:v1'),
            ('other-owner', 'creation_job', 'workspace', 'member', 'cpu-prepare:v1')])
    before = path.read_bytes()
    actor = CreationExecutionActor('workspace', 'member')
    assert request_kind(database_path=path, request_id='cpu', actor=actor) == 'cpu'
    assert request_kind(database_path=path, request_id='voice', actor=actor) == 'narration'
    with pytest.raises(ValueError, match='Unknown'):
        request_kind(database_path=path, request_id='unknown', actor=actor)
    with pytest.raises(KeyError):
        request_kind(database_path=path, request_id='missing', actor=actor)
    for identity, request_id in ((actor, 'other-owner'),
            (CreationExecutionActor('other-workspace', 'member'), 'cpu'),
            (CreationExecutionActor('workspace', 'other-member'), 'voice')):
        with pytest.raises(PermissionError):
            request_kind(database_path=path, request_id=request_id, actor=identity)
    assert path.read_bytes() == before
    absent = tmp_path / 'absent.sqlite'
    with pytest.raises(sqlite3.OperationalError):
        request_kind(database_path=absent, request_id='cpu', actor=actor)
    assert not absent.exists()
