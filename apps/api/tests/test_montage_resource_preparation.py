"""Owner/receipt contracts with declared samples; no real measurement or driver."""
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
from types import SimpleNamespace

import pytest

import app.montage_resource_preparation as module
from app.device_resource_profile import DeviceProfileUnavailable
from app.device_runtime import build_device_runtime
from app.device_scheduler import RecoveryBlocked
from app.host_resource_probes import HostResourceProbe, montage_scope_digest
from app.montage_resource_preparation import MontagePreparationShape, MontageResourcePreparation
from tests.host_memory_boundary import IsolatedMemoryMonitor
from test_device_resource_preparation import metadata_preparation
from test_device_resource_profile import profile_document
from test_device_scheduler import Processes, Resources


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    repository, actor, access, cpu, paths = metadata_preparation(tmp_path, monkeypatch)
    document = profile_document()
    document['device'] = asdict(cpu.device)
    cpu.profile_path.parent.mkdir(parents=True, exist_ok=True)
    cpu.profile_path.write_text(json.dumps(document))
    facts = {'task_id': 'task', 'script_id': 'script', 'dependency_digest': 'original-dependency',
             'manifest_sha256': 'full-workspace-manifest', 'input_bytes': 4096,
             'section_count': 2, 'asset_count': 3, 'authorized_asset_ids': ['allowed'],
             'eligible_segment_count': 2}
    calls = []

    def build(workspace_id, task_id, *, grant_context, authorized_asset_ids):
        grant_context.check()
        assert workspace_id == actor.workspace_id and task_id == 'task'
        assert authorized_asset_ids == frozenset({'allowed'})
        assert set(grant_context.scheduler.owners.registrations) == {'device_preparation'}
        calls.append(task_id)
        return {'clips': [{'asset_id': 'allowed'}], 'cues': ['one', 'two']}

    stage = SimpleNamespace(build_candidate=build)
    authorization = SimpleNamespace(verdict=lambda *args: 'ready', material_asset_ids=lambda *args: {'allowed'})
    service = MontageResourcePreparation(repository=repository, montage_stage=stage, task_authorizations=authorization,
        profile_path=cpu.profile_path, device=cpu.device, settings=cpu.settings, write_paths=paths,
        verify_identity=access.verify_identity)
    monkeypatch.setattr(service, '_inputs', lambda shape, selected: deepcopy(facts))
    monkeypatch.setattr(module, 'sample_host_memory', lambda: SimpleNamespace(pressure='normal', available_bytes=4 * 1024**3))
    monkeypatch.setattr(module, 'creation_task_montage_plan_to_payload', lambda candidate: candidate)
    observed = {'scope': 'whole_api_process_rss_not_incremental_allocation',
                'peak_rss_bytes': 128 * 1024**2, 'sample_count': 2,
                'sample_interval_seconds': .05, 'longest_sample_gap_seconds': .01, 'rss_sample_complete': True}

    class DeclaredObservation:
        def __init__(self, interval, max_gap):
            assert interval == .05 and max_gap == .25

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def payload(self):
            return dict(observed)

    monkeypatch.setattr(module, '_RssObservation', DeclaredObservation)
    plan_id, budget = service.prepare(MontagePreparationShape('task'), actor, request_id='same-request')
    runtime = build_device_runtime(database_path=repository.database_path, device_lock_path=tmp_path / 'device.lock',
        device_access=access, registrations={'device_preparation': service.registration()},
        resources=Resources(), processes=Processes())
    request = runtime.scheduler.submit(service.intent(plan_id, actor), actor)
    result = SimpleNamespace(repository=repository, actor=actor, access=access, service=service, calls=calls,
        runtime=runtime, request=request, plan_id=plan_id, budget=budget, original=document,
        facts=facts, observed=observed, stage=stage, authorization=authorization)
    yield result
    assert runtime.stop(2)


def test_preparation_binds_task_and_server_manifest_without_start_or_publication(prepared):
    case = prepared
    service = case.service
    plan = service._plan(case.plan_id)
    assert plan['shape'] == {'task_id': 'task'}
    assert plan['inputs'] == case.facts
    assert plan['host_observation']['scope'] == 'bounded_preparation_allowance_not_measured_capacity'
    assert case.budget.montage.max_input_bytes == 4096
    assert case.budget.montage.additional_memory_bytes == (4 * 1024**3 - 1024**3) // 2
    assert case.budget.montage.max_timeout_seconds == 30
    assert json.loads(service.profile_path.read_bytes()) == case.original
    assert not case.calls and not case.runtime.running
    assert service.prepare(MontagePreparationShape('task'), case.actor, request_id='same-request')[0] == case.plan_id
    with pytest.raises(ValueError, match='输入已变化'):
        service.prepare(MontagePreparationShape('another-task'), case.actor, request_id='same-request')
    with pytest.raises(TypeError):
        MontagePreparationShape('task', asset_count=100)
    case.facts['dependency_digest'] = 'changed-current-script'
    assert service.check_current(case.request, None) == 'dependency'
    assert case.runtime.scheduler.pick_and_grant() is None
    assert not case.calls


def test_original_checkpoint_publishes_only_montage_and_retains_receipt_evidence(prepared):
    case = prepared
    with pytest.raises(DeviceProfileUnavailable, match='完成回执'):
        case.service.finalize(database_path=case.repository.database_path, request_id=case.request.id, actor=case.actor)
    grant = case.runtime.scheduler.pick_and_grant()
    case.service.execute(grant, case.runtime.scheduler)
    assert case.calls == ['task']
    assert case.service.status(database_path=case.repository.database_path, request_id=case.request.id, actor=case.actor)['state'] == 'done'
    assert json.loads(case.service.profile_path.read_bytes()) == case.original
    result = case.service.finalize(database_path=case.repository.database_path, request_id=case.request.id, actor=case.actor)
    document = json.loads(case.service.profile_path.read_bytes())
    assert document['budget'] == {**case.original['budget'], 'montage': asdict(result.budget.montage)}
    assert document['backends'] == {**case.original['backends'], 'montage': case.service._identity()}
    assert result.budget.montage.additional_memory_bytes == 256 * 1024**2
    assert result.budget.montage.max_section_count == 2 and result.budget.montage.max_asset_count == 3
    assert result.budget.montage.preparation_basis_digest == montage_scope_digest(
        task_id=case.facts['task_id'], dependency_digest=case.facts['dependency_digest'],
        manifest_sha256=case.facts['manifest_sha256'], authorized_asset_ids=case.facts['authorized_asset_ids'])
    assert result.evidence['montage']['status'] == 'recorded'
    assert result.evidence['montage']['verification_status'] == 'unknown'
    evidence = json.loads((case.service.profile_path.parent / 'resource-profile-evidence' /
        (document['evidence']['montage']['record_id'] + '.json')).read_bytes())
    assert evidence['input_range']['task_id'] == 'task'
    assert evidence['input_range']['receipt_sha256']
    assert evidence['observations']['candidate']['bytes'] > 0
    assert len(evidence['observations']['candidate']['sha256']) == 64
    again = case.service.finalize(database_path=case.repository.database_path, request_id=case.request.id, actor=case.actor)
    assert (again.digest, again.revision, case.calls) == (result.digest, result.revision, ['task'])
    receipt = next((case.service.root / 'receipts').glob('*.json'))
    receipt.write_text('{}')
    with pytest.raises(DeviceProfileUnavailable, match='检查点不匹配'):
        case.service.finalize(database_path=case.repository.database_path, request_id=case.request.id, actor=case.actor)
    assert case.service._load().digest == result.digest


@pytest.mark.parametrize('change', [None, 'task_id', 'dependency_digest', 'manifest_sha256', 'authorized_asset_ids', 'missing'])
def test_measured_budget_admits_only_original_scope_even_when_counts_and_bytes_match(prepared, monkeypatch, change):
    case = prepared
    case.service.execute(case.runtime.scheduler.pick_and_grant(), case.runtime.scheduler)
    profile = case.service.finalize(database_path=case.repository.database_path,
                                    request_id=case.request.id, actor=case.actor)
    limit = profile.budget.montage
    scope = {name: case.facts[name] for name in
             ('task_id', 'dependency_digest', 'manifest_sha256', 'authorized_asset_ids')}
    if change == 'authorized_asset_ids':
        scope[change] = ['a-different-authorized-asset']
    elif change not in {None, 'missing'}:
        scope[change] = 'a-different-input-with-identical-counts-and-bytes'
    resources = {**case.service._resources(case.service._plan(case.plan_id)),
                 'max_output_bytes': limit.max_output_bytes, 'timeout_seconds': limit.max_timeout_seconds}
    if change != 'missing':
        resources['preparation_basis_digest'] = montage_scope_digest(**scope)
    probe = HostResourceProbe(case.repository.database_path, disk_paths=case.service.write_paths,
                              budget=profile.budget, memory_monitor=IsolatedMemoryMonitor())
    # These are declaration-boundary cases, not native pressure measurements.
    monkeypatch.setattr(module.psutil, 'cpu_percent', lambda **kwargs: 0)
    monkeypatch.setattr(module.psutil, 'disk_io_counters', lambda **kwargs: SimpleNamespace(read_bytes=0, write_bytes=0))
    monkeypatch.setattr(module.psutil, 'disk_usage', lambda path: SimpleNamespace(free=16 * 1024**3))
    monkeypatch.setattr(module.psutil, 'virtual_memory', lambda: SimpleNamespace(available=4 * 1024**3))
    monkeypatch.setattr(module.psutil, 'swap_memory', lambda: SimpleNamespace(used=0))
    decision = probe.admit(SimpleNamespace(id='scope-request', owner_type='creation_job',
                                          unit_key='montage_plan:current', resources=resources))
    if change is None:
        assert decision.verdict is True
    else:
        assert decision.verdict is False and decision.reason == 'montage_preparation_binding_changed'
    assert resources['section_count'] == 2 and resources['asset_count'] == 3 and resources['input_bytes'] == 4096


def test_all_person_metadata_can_declare_zero_materials_without_inventing_assets(prepared):
    case = prepared
    case.facts.update(asset_count=0, authorized_asset_ids=[], eligible_segment_count=0)
    plan_id, budget = case.service.prepare(MontagePreparationShape('task'), case.actor, request_id='all-person')
    plan = case.service._plan(plan_id)
    assert plan['inputs']['asset_count'] == 0 and plan['inputs']['eligible_segment_count'] == 0
    assert case.service._resources(plan)['asset_count'] == 0
    assert budget.montage.max_asset_count == 1  # Positive declaration floor, not a measured asset.
    request = case.runtime.scheduler.submit(case.service.intent(plan_id, case.actor), case.actor)

    def all_person(*args, **kwargs):
        assert kwargs['authorized_asset_ids'] == frozenset()
        kwargs['grant_context'].check()
        return {'clips': [{'person_render_id': 'already-authorized-render'}], 'cues': ['one', 'two']}

    case.stage.build_candidate = all_person
    case.service.execute(case.runtime.scheduler.pick_and_grant(), case.runtime.scheduler)
    assert case.service.status(database_path=case.repository.database_path, request_id=request.id, actor=case.actor)['state'] == 'done'
    assert json.loads(case.service.profile_path.read_bytes()) == case.original


@pytest.mark.parametrize('failure', ['incomplete', 'gap', 'rss', 'output', 'stop', 'deadline'])
def test_known_measurement_failure_never_publishes_or_replaces_original_request(prepared, failure):
    case = prepared
    grant = case.runtime.scheduler.pick_and_grant()
    if failure == 'incomplete':
        case.observed['rss_sample_complete'] = False
    elif failure == 'gap':
        case.observed['longest_sample_gap_seconds'] = 1.0
    elif failure == 'rss':
        case.observed['peak_rss_bytes'] = case.budget.montage.additional_memory_bytes + 1
    else:
        original_build = case.stage.build_candidate

        def build(*args, **kwargs):
            candidate = original_build(*args, **kwargs)
            if failure == 'output':
                return {'huge': 'x' * case.budget.montage.max_output_bytes}
            if failure == 'stop':
                case.service.request_stop()
            else:
                from app.granted_subprocess import GrantedCommandTimeout
                raise GrantedCommandTimeout('finite metadata deadline')
            return candidate

        case.stage.build_candidate = build
    case.service.execute(grant, case.runtime.scheduler)
    status = case.service.status(database_path=case.repository.database_path, request_id=case.request.id, actor=case.actor)
    assert status['state'] == 'failed'
    assert case.runtime.scheduler.shutdown_released()
    assert json.loads(case.service.profile_path.read_bytes()) == case.original
    assert not list((case.service.root / 'receipts').glob('*.json'))
    assert case.service.prepare(MontagePreparationShape('task'), case.actor, request_id='same-request')[0] == case.plan_id
    assert case.runtime.scheduler.submit(case.service.intent(case.plan_id, case.actor), case.actor).id == case.request.id
    with pytest.raises(DeviceProfileUnavailable, match='完成回执'):
        case.service.finalize(database_path=case.repository.database_path, request_id=case.request.id, actor=case.actor)


def test_started_without_receipt_is_unknown_and_cannot_repeat_candidate(prepared):
    case = prepared
    grant = case.runtime.scheduler.pick_and_grant()

    def interrupted(*args, **kwargs):
        case.calls.append('interrupted')
        raise RecoveryBlocked('simulated process interruption after original start')

    case.stage.build_candidate = interrupted
    with pytest.raises(RecoveryBlocked):
        case.service.execute(grant, case.runtime.scheduler)
    with pytest.raises(RecoveryBlocked, match='禁止重跑'):
        case.service.execute(grant, case.runtime.scheduler)
    assert case.calls == ['interrupted']
    assert json.loads(case.service.profile_path.read_bytes()) == case.original
    assert not list((case.service.root / 'receipts').glob('*.json'))
    # Retained permit is expected; clean this isolated fixture only after the
    # assertion, using its known in-process/no-child release path.
    case.runtime.scheduler.fail_unit(grant, reason='fixture_cleanup', failure_write=lambda conn, current: None)


def test_receipt_reentry_only_finishes_original_checkpoint_after_uncertain_commit(prepared, monkeypatch):
    case = prepared
    grant = case.runtime.scheduler.pick_and_grant()
    original_complete = case.runtime.scheduler.complete_unit

    def unavailable(*args, **kwargs):
        raise OSError('simulated unknown checkpoint transaction')

    monkeypatch.setattr(case.runtime.scheduler, 'complete_unit', unavailable)
    with pytest.raises(OSError, match='unknown checkpoint'):
        case.service.execute(grant, case.runtime.scheduler)
    assert case.calls == ['task']
    assert len(list((case.service.root / 'receipts').glob('*.json'))) == 1
    assert json.loads(case.service.profile_path.read_bytes()) == case.original
    monkeypatch.setattr(case.runtime.scheduler, 'complete_unit', original_complete)
    case.service.execute(grant, case.runtime.scheduler)
    assert case.calls == ['task']
    assert case.service.status(database_path=case.repository.database_path, request_id=case.request.id, actor=case.actor)['state'] == 'done'


@pytest.mark.parametrize('change', ['profile', 'dependency', 'member'])
def test_completed_receipt_cannot_overwrite_new_profile_or_changed_authorization(prepared, change, monkeypatch):
    case = prepared
    case.service.execute(case.runtime.scheduler.pick_and_grant(), case.runtime.scheduler)
    if change == 'profile':
        document = deepcopy(case.original)
        document['revision'] += 1
        document['budget']['max_cpu_percent'] = 55
        case.service.profile_path.write_text(json.dumps(document))
    elif change == 'dependency':
        case.facts['dependency_digest'] = 'a-newer-user-edit'
    else:
        monkeypatch.setattr(case.service, 'verify_identity', lambda actor: 'unauthorized')
    before = case.service.profile_path.read_bytes()
    with pytest.raises((DeviceProfileUnavailable, ValueError, PermissionError)):
        case.service.finalize(database_path=case.repository.database_path, request_id=case.request.id, actor=case.actor)
    assert case.service.profile_path.read_bytes() == before


def test_server_shape_counts_complete_manifest_but_filters_candidate_material_scope(prepared, monkeypatch):
    case = prepared

    @dataclass
    class Row:
        id: str

    assets = [Row('allowed'), Row('private-workspace-only')]
    sections = [Row('section-one'), Row('section-two')]
    task = SimpleNamespace(id='task', workspace_id=case.actor.workspace_id, owner_user_id=case.actor.member_id,
                           narration_cache_key='narration')
    header = SimpleNamespace(id='script', sections_total_count=2)
    snapshot = SimpleNamespace(task=task, script_header=header)
    repository = case.repository
    monkeypatch.setattr(module, 'montage_dependency', lambda *args, **kwargs: (snapshot, 'real-dependency'))
    monkeypatch.setattr(repository.task_records, 'get_creation_task', lambda *args: task)
    monkeypatch.setattr(repository.script_records, 'read_execution_script_metadata', lambda *args: (header, {'id': 'script'}))
    monkeypatch.setattr(repository.script_records, 'iter_execution_script_sections', lambda *args: iter(sections))
    monkeypatch.setattr(repository.narration_artifacts, 'get_narration_artifact', lambda *args, **kwargs: Row('narration'))
    monkeypatch.setattr(repository, 'list_assets', lambda *args, **kwargs: assets)

    def selected(*args, **kwargs):
        assert kwargs['asset_ids'] == frozenset({'allowed'})
        assert kwargs['viewer_user_id'] == case.actor.member_id
        return iter([(assets[0], Row('one')), (assets[0], Row('two'))])

    monkeypatch.setattr(repository, 'automatic_material_segments', selected)
    facts = MontageResourcePreparation._inputs(case.service, MontagePreparationShape('task'), case.actor)
    assert facts['section_count'] == 2 and facts['asset_count'] == 2 and facts['eligible_segment_count'] == 2
    full_manifest = b''.join(module.montage_manifest_chunks(assets, Row('narration'), {'id': 'script'}, sections))
    assert facts['input_bytes'] == len(full_manifest)
    assert facts['authorized_asset_ids'] == ['allowed']
    case.authorization.verdict = lambda *args: 'unknown'
    with pytest.raises(PermissionError, match='原任务'):
        MontageResourcePreparation._inputs(case.service, MontagePreparationShape('task'), case.actor)


def test_rss_observation_keeps_total_residency_and_marks_unobserved_gap_unknown(monkeypatch):
    rss = iter([80, 120, 100])
    times = iter([0.0, .05, .6])
    monkeypatch.setattr(module.psutil, 'Process', lambda: SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=next(rss))))
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(times))
    observation = module._RssObservation(.05, .25)
    observation.sample()
    observation.sample()
    assert observation.payload()['rss_sample_complete']
    assert observation.payload()['peak_rss_bytes'] == 120  # Never subtract baseline 80.
    observation.sample()
    assert observation.payload()['rss_sample_complete'] is False
    assert observation.payload()['longest_sample_gap_seconds'] > .25
