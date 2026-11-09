"""Measure one authorized metadata storyboard without adopting a task result.

The existing device owner supplies the permit, durable request and publication
CAS. Preparation limits are engineering bounds, not measured film capacity.
Like normal metadata planning, cancellation and limits apply at safe points;
this module does not preempt an individual Python planning calculation.
"""
from dataclasses import asdict, dataclass, replace
from copy import deepcopy
import hashlib
import json
import math
import threading
import time

import psutil

from app.creation.montage_stage_use_case import montage_dependency
from app.creation_execution import CreationExecutionActor, CreationStageGrantContext, montage_manifest_chunks
from app.device_resource_configuration import backend_identity
from app.device_resource_preparation import (
    DeviceResourcePreparation, OWNER_TYPE, _atomic_json, _profile_lock, _profile_state, _read,
)
from app.device_resource_profile import (
    DeviceProfileUnavailable, _digest, host_budget_from_payload, load_device_resource_profile,
)
from app.device_resource_volumes import sample_write_volumes
from app.device_scheduler import RecoveryBlocked, SchedulePolicy, SubmissionIntent, VerifiedWork
from app.granted_subprocess import GrantedCommandError
from app.host_memory import sample_host_memory
from app.host_resource_probes import MontageBudget, montage_scope_digest
from app.montage import creation_task_montage_plan_to_payload


UNIT_KEY = 'montage-prepare:v1'
POLICY_ID = 'metadata-storyboard-preparation/v1'
CHECKPOINT_PREFIX = 'montage-resource-preparation:'


@dataclass(frozen=True)
class MontagePreparationShape:
    task_id: str

    def __post_init__(self):
        if not isinstance(self.task_id, str) or not self.task_id.strip() or len(self.task_id) > 200:
            raise ValueError('故事板准备需要明确的原任务身份')


@dataclass(frozen=True)
class MontagePreparationPolicy:
    """Finite preparation allowance, never a CPU-derived montage calibration.

    Half the currently available headroom after the existing host reserve is an
    admission allowance. Four input manifests plus 64 KiB bound candidate JSON;
    the full candidate is measured before a twofold observed margin is recorded.
    RSS is the whole API process, conservatively including unrelated residency.
    """
    id: str = POLICY_ID
    timeout_seconds: float = 30.0
    memory_fraction: float = .5
    output_input_multiplier: int = 4
    output_allowance_bytes: int = 65536
    measured_margin: float = 2.0
    sample_seconds: float = .05
    max_sample_gap_seconds: float = .25

    def __post_init__(self):
        if (self.id != POLICY_ID or not 0 < self.memory_fraction < 1
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
                or not math.isfinite(self.measured_margin) or self.measured_margin < 1
                or not 0 < self.sample_seconds <= .25
                or not math.isfinite(self.max_sample_gap_seconds)
                or self.max_sample_gap_seconds < self.sample_seconds
                or any(type(value) is not int or value <= 0 for value in
                       (self.output_input_multiplier, self.output_allowance_bytes))):
            raise ValueError('Invalid bounded metadata preparation policy')


class _RssObservation:
    """Sample this process for this one in-process preparation interval."""
    def __init__(self, interval, max_gap):
        self.interval = interval
        self.max_gap = max_gap
        self.process = psutil.Process()
        self.peak = 0
        self.count = 0
        self.error = None
        self.previous_sample = None
        self.longest_gap = 0.0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._poll, name='montage-preparation-rss', daemon=True)

    def sample(self):
        try:
            rss = self.process.memory_info().rss
            if type(rss) is not int or rss <= 0:
                raise ValueError('未知的本进程RSS')
            now = time.monotonic()
            if self.previous_sample is not None:
                self.longest_gap = max(self.longest_gap, now - self.previous_sample)
            self.previous_sample = now
            self.peak = max(self.peak, rss)
            self.count += 1
        except (psutil.Error, OSError, ValueError) as exc:
            self.error = exc

    def _poll(self):
        while not self.stop.wait(self.interval):
            self.sample()

    def __enter__(self):
        self.sample()
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()
        self.sample()

    def payload(self):
        return {'scope': 'whole_api_process_rss_not_incremental_allocation',
                'peak_rss_bytes': self.peak, 'sample_count': self.count,
                'sample_interval_seconds': self.interval,
                'longest_sample_gap_seconds': self.longest_gap,
                'rss_sample_complete': (self.error is None and self.count >= 2
                                        and self.longest_gap <= self.max_gap)}


class MontageResourcePreparation(DeviceResourcePreparation):
    def __init__(self, *, repository, montage_stage, task_authorizations,
                 policy=MontagePreparationPolicy(), **kwargs):
        super().__init__(policy=policy, **kwargs)
        self.repository, self.montage_stage = repository, montage_stage
        self.task_authorizations = task_authorizations
        self.root = self.profile_path.parent / 'resource-preparations' / 'montage'
        self._active_lock = threading.Lock()
        self._cancel = threading.Event()
        self._active = False

    def prepare(self, shape, actor, *, request_id):
        if not isinstance(shape, MontagePreparationShape):
            raise ValueError('Montage preparation requires its typed task input')
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise ValueError('Preparation needs a bounded stable request identity')
        with self._active_lock:
            if not self._active:
                # A later explicit prepare may bind a fresh runtime. execute()
                # never clears shutdown delivered between dispatch and entry.
                self._cancel.clear()
        return super().prepare(shape, actor, request_id='montage:' + _digest(request_id))

    def _identity(self):
        return backend_identity('montage', None, settings=self.settings)

    def _inputs(self, shape, actor):
        if self.verify_identity(actor) != 'ready':
            raise PermissionError('当前成员身份不可用于故事板准备')
        if self.task_authorizations.verdict(actor, shape.task_id, 'montage_plan') != 'ready':
            raise PermissionError('原任务的故事板授权尚未就绪')
        asset_ids = sorted(self.task_authorizations.material_asset_ids(actor.workspace_id, shape.task_id))
        task = self.repository.task_records.get_creation_task(actor.workspace_id, shape.task_id)
        with self.repository.execution_inputs.creation_execution_read() as conn:
            snapshot, dependency = montage_dependency(self.repository, task, connection=conn)
            task, header = snapshot.task, snapshot.script_header
            _, fields = self.repository.script_records.read_execution_script_metadata(conn, task.workspace_id, header.id)
            narration = self.repository.narration_artifacts.get_narration_artifact(
                task.workspace_id, task.narration_cache_key, connection=conn)
            # Admission uses the same complete workspace manifest as normal
            # creation_stage_facts; actual selection stays task-authorized.
            assets = self.repository.list_assets(task.workspace_id, connection=conn)
            checksum, input_bytes = hashlib.sha256(), 0
            for chunk in montage_manifest_chunks(assets, narration, fields,
                    self.repository.script_records.iter_execution_script_sections(conn, task.workspace_id, header.id)):
                checksum.update(chunk)
                input_bytes += len(chunk)
            eligible_segments = sum(1 for _ in self.repository.automatic_material_segments(task.workspace_id,
                viewer_user_id=task.owner_user_id, asset_ids=frozenset(asset_ids), connection=conn))
            return {'task_id': task.id, 'script_id': header.id, 'dependency_digest': dependency,
                    'manifest_sha256': checksum.hexdigest(), 'input_bytes': input_bytes,
                    'section_count': header.sections_total_count, 'asset_count': len(assets),
                    'authorized_asset_ids': asset_ids, 'eligible_segment_count': eligible_segments}

    def _load(self, path=None):
        return load_device_resource_profile(path or self.profile_path, device=self.device,
            backend_identities=lambda name, operation: backend_identity(name, operation, settings=self.settings))

    def _prepare_new(self, shape, actor, *, plan_id, request_binding):
        identity = self._identity()
        if identity is None:
            raise DeviceProfileUnavailable('故事板元数据实现身份不可读取，请先修复应用')
        inputs = self._inputs(shape, actor)
        if not inputs['section_count']:
            raise DeviceProfileUnavailable('当前任务缺少定稿段落，未开始故事板准备')
        with _profile_lock(self.profile_path):
            current = self._load()
            revision, digest = _profile_state(self.profile_path)
        memory = sample_host_memory()
        if (memory.available_bytes is None or memory.pressure not in {'normal', 'warning'}
                or memory.available_bytes <= current.budget.min_available_memory_bytes):
            raise DeviceProfileUnavailable('当前系统内存压力不可供有限故事板准备')
        allowance = math.floor((memory.available_bytes - current.budget.min_available_memory_bytes)
                               * self.policy.memory_fraction)
        output = min(allowance, inputs['input_bytes'] * self.policy.output_input_multiplier
                     + self.policy.output_allowance_bytes)
        # The budget type requires a positive asset upper bound. An all-person
        # candidate may genuinely read zero material assets; resources/evidence
        # retain that zero and the real builder decides whether it can cover it.
        limit = MontageBudget(inputs['section_count'], max(1, inputs['asset_count']), inputs['input_bytes'],
                              output, self.policy.timeout_seconds, allowance)
        budget = replace(current.budget, montage=limit)
        reservations = {'database': 0, 'output': 0, 'temporary': output}
        disks = sample_write_volumes(self.write_paths, reservations,
            reserve_bytes=budget.min_free_disk_bytes, verify_writable=True)
        if not all(item['passed'] for item in disks.values()):
            raise DeviceProfileUnavailable('故事板准备的实际写入卷空间不足或不可写')
        plan = {'format_version': 1, 'id': plan_id, 'kind': 'montage', 'device': asdict(self.device),
                'request_binding': request_binding, 'actor_identity': actor.identity_payload(),
                'workspace_id': actor.workspace_id, 'member_id': actor.member_id,
                'shape': asdict(shape), 'inputs': inputs, 'policy': asdict(self.policy),
                'bootstrap_budget': asdict(budget), 'backends': {'montage': identity},
                'write_paths': self.write_paths, 'write_reservations': reservations,
                'host_observation': {'pressure': memory.pressure, 'available_bytes': memory.available_bytes,
                    'scope': 'bounded_preparation_allowance_not_measured_capacity'},
                'expected_revision': revision, 'expected_profile_digest': digest}
        plan['input_digest'] = _digest(plan)
        _atomic_json(self.root / 'plans' / f'{plan_id}.json', plan)
        return plan_id, budget

    def bootstrap_budget(self, plan_id):
        return host_budget_from_payload(self._plan(plan_id)['bootstrap_budget'])

    def intent(self, plan_id, actor):
        plan = self._plan(plan_id)
        if not self._can_access(plan, actor):
            raise PermissionError('故事板准备属于其他成员')
        return SubmissionIntent(plan['workspace_id'], OWNER_TYPE, plan_id, plan['input_digest'], UNIT_KEY, SchedulePolicy())

    def _resources(self, plan):
        limit = self.bootstrap_budget(plan['id']).montage
        return {'kind': 'creation_montage_plan', 'execution_mode': 'metadata_only',
                'section_count': plan['inputs']['section_count'], 'asset_count': plan['inputs']['asset_count'],
                'input_bytes': plan['inputs']['input_bytes'], 'max_output_bytes': limit.max_output_bytes,
                'timeout_seconds': limit.max_timeout_seconds}

    def verify_submission(self, intent, actor):
        if intent != self.intent(intent.owner_id, actor):
            raise PermissionError('故事板准备提交不匹配原计划')
        plan = self._plan(intent.owner_id)
        return VerifiedWork(plan['workspace_id'], OWNER_TYPE, plan['id'], plan['input_digest'], UNIT_KEY,
                            plan['member_id'], 'normal_make', self._resources(plan))

    def _recheck(self, plan, actor):
        if not self._can_access(plan, actor):
            raise PermissionError('故事板准备的成员授权已变化')
        if (plan['device'] != asdict(self.device) or self._identity() != plan['backends']['montage']
                or plan['write_paths'] != self.write_paths
                or self._inputs(MontagePreparationShape(**plan['shape']), actor) != plan['inputs']):
            raise ValueError('故事板准备的原任务输入或实现已变化')

    def check_current(self, request, conn):
        try:
            plan = self._plan(request.owner_id)
            if ((request.workspace_id, request.member_id, request.owner_type, request.unit_key, request.input_digest)
                    != (plan['workspace_id'], plan['member_id'], OWNER_TYPE, UNIT_KEY, plan['input_digest'])
                    or request.resources != self._resources(plan)
                    or _profile_state(self.profile_path) != (plan['expected_revision'], plan['expected_profile_digest'])):
                return 'stale'
            self._recheck(plan, CreationExecutionActor.from_event(plan['workspace_id'], plan['actor_identity']))
            return 'ready'
        except PermissionError:
            return 'unauthorized'
        except (OSError, KeyError, ValueError, DeviceProfileUnavailable):
            return 'dependency'

    def request_stop(self):
        with self._active_lock:
            self._cancel.set()

    def _validate_receipt(self, plan, receipt, *, request_id, generation):
        if (any(receipt[key] != plan[key] for key in ('device', 'backends', 'shape', 'inputs', 'input_digest', 'policy'))
                or (receipt['plan_id'], receipt['request_id'], receipt['generation'])
                    != (plan['id'], request_id, generation)):
            raise DeviceProfileUnavailable('故事板准备回执不匹配原计划或许可')
        limit = self.bootstrap_budget(plan['id']).montage
        observed = receipt['observed_resources']
        output = receipt['candidate']
        if (observed['scope'] != 'whole_api_process_rss_not_incremental_allocation'
                or observed['rss_sample_complete'] is not True
                or observed['sample_interval_seconds'] != plan['policy']['sample_seconds']
                or type(observed['longest_sample_gap_seconds']) not in {float, int}
                or not 0 <= observed['longest_sample_gap_seconds'] <= plan['policy']['max_sample_gap_seconds']
                or type(observed['sample_count']) is not int or observed['sample_count'] < 2
                or type(observed['peak_rss_bytes']) is not int
                or not 0 < observed['peak_rss_bytes'] <= limit.additional_memory_bytes
                or type(receipt['elapsed_seconds']) not in {float, int}
                or not math.isfinite(receipt['elapsed_seconds'])
                or not 0 < receipt['elapsed_seconds'] < limit.max_timeout_seconds
                or type(output['bytes']) is not int or not 0 < output['bytes'] <= limit.max_output_bytes
                or not isinstance(output['sha256'], str) or len(output['sha256']) != 64
                or any(char not in '0123456789abcdef' for char in output['sha256'])):
            raise DeviceProfileUnavailable('故事板准备采样不完整或已超出有限准备范围')

    def execute(self, grant, scheduler):
        plan = self._plan(grant.owner_id)
        actor = CreationExecutionActor.from_event(plan['workspace_id'], plan['actor_identity'])
        limit = self.bootstrap_budget(plan['id']).montage
        started = time.monotonic()
        with self._active_lock:
            self._active = True
        context = CreationStageGrantContext(scheduler, grant, started + limit.max_timeout_seconds,
                                            limit.max_output_bytes, 1, cancel_event=self._cancel)
        receipt_path = self.root / 'receipts' / f'{grant.request_id}-{grant.generation}.json'
        marker_path = self.root / 'started' / f'{grant.request_id}.json'
        try:
            context.check()
            self._recheck(plan, actor)
            if marker_path.exists():
                if not receipt_path.exists():
                    raise RecoveryBlocked('原故事板准备已开始但无可信完成回执，请核查原请求，禁止重跑')
                receipt = _read(receipt_path)
                self._validate_receipt(plan, receipt, request_id=grant.request_id, generation=grant.generation)
                digest = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            else:
                _atomic_json(marker_path, {'request_id': grant.request_id, 'generation': grant.generation,
                                          'input_digest': grant.input_digest})
                with _RssObservation(self.policy.sample_seconds, self.policy.max_sample_gap_seconds) as observation:
                    candidate = self.montage_stage.build_candidate(plan['workspace_id'], plan['shape']['task_id'],
                        grant_context=context, authorized_asset_ids=frozenset(plan['inputs']['authorized_asset_ids']))
                    context.check()
                    raw = json.dumps(creation_task_montage_plan_to_payload(candidate)).encode()
                    context.check()
                self._recheck(plan, actor)
                context.check()
                receipt = {'format_version': 1, 'plan_id': plan['id'], 'request_id': grant.request_id,
                           'generation': grant.generation,
                           **{key: plan[key] for key in ('input_digest', 'device', 'backends', 'shape', 'inputs', 'policy')},
                           'elapsed_seconds': time.monotonic() - started,
                           'observed_resources': observation.payload(),
                           'candidate': {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}
                self._validate_receipt(plan, receipt, request_id=grant.request_id, generation=grant.generation)
                digest = _atomic_json(receipt_path, receipt)
            context.check()
        except (GrantedCommandError, OSError, ValueError, KeyError, DeviceProfileUnavailable):
            # RecoveryBlocked (including uncertain commit/release) deliberately
            # escapes. Neither a profile nor a replacement request is invented.
            scheduler.fail_unit(grant, reason='montage_preparation_failed', failure_write=lambda conn, current: None)
            return
        finally:
            with self._active_lock:
                self._active = False
        # A failed/uncertain completion transaction is not a known measurement
        # failure. Preserve its original receipt and permit for reconciliation.
        scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
            checkpoint_write=lambda conn, current: CHECKPOINT_PREFIX + digest)

    def finalize(self, *, database_path, request_id, actor):
        row, plan = self._request(database_path=database_path, request_id=request_id, actor=actor)
        if row['state'] != 'done':
            raise DeviceProfileUnavailable('故事板准备尚无完成回执，请读取原请求')
        raw = (self.root / 'receipts' / f"{row['id']}-{row['last_grant_generation']}.json").read_bytes()
        receipt_digest = hashlib.sha256(raw).hexdigest()
        if (row['checkpoint_ref'] != CHECKPOINT_PREFIX + receipt_digest
                or (row['workspace_id'], row['member_id'], row['unit_key'], row['input_digest'])
                    != (plan['workspace_id'], plan['member_id'], UNIT_KEY, plan['input_digest'])
                or json.loads(row['resources_json']) != self._resources(plan)):
            raise DeviceProfileUnavailable('故事板准备回执与原请求检查点不匹配')
        receipt = json.loads(raw)
        self._validate_receipt(plan, receipt, request_id=row['id'], generation=row['last_grant_generation'])
        self._recheck(plan, actor)
        original = self.bootstrap_budget(plan['id']).montage
        margin = plan['policy']['measured_margin']
        prepared = replace(original,
            preparation_basis_digest=montage_scope_digest(task_id=plan['inputs']['task_id'],
                dependency_digest=plan['inputs']['dependency_digest'],
                manifest_sha256=plan['inputs']['manifest_sha256'],
                authorized_asset_ids=plan['inputs']['authorized_asset_ids']),
            max_output_bytes=min(original.max_output_bytes, math.ceil(receipt['candidate']['bytes'] * margin)),
            max_timeout_seconds=min(original.max_timeout_seconds,
                max(self.policy.sample_seconds, receipt['elapsed_seconds'] * margin)),
            additional_memory_bytes=min(original.additional_memory_bytes,
                math.ceil(receipt['observed_resources']['peak_rss_bytes'] * margin)))
        with _profile_lock(self.profile_path):
            document = _read(self.profile_path)
            if document['profile_id'] == plan['id']:
                return self._load()
            if (document['revision'], _digest(document)) != (plan['expected_revision'], plan['expected_profile_digest']):
                raise DeviceProfileUnavailable('设备档已更新，原故事板准备回执保留，不能覆盖新版本')
            old = deepcopy(document)
            document.update(profile_id=plan['id'], revision=document['revision'] + 1)
            document['budget']['montage'] = asdict(prepared)
            document['backends']['montage'] = plan['backends']['montage']
            evidence = {'format_version': 1, 'device': plan['device'],
                'backend_identity': plan['backends']['montage'], 'budget_digest': _digest(asdict(prepared)),
                'method': plan['policy']['id'],
                'input_range': {'task_id': plan['shape']['task_id'], **plan['inputs'], 'policy': plan['policy'],
                    'scope': 'one_recorded_authorized_metadata_candidate_not_whole_film_capacity',
                    'receipt_sha256': receipt_digest}, 'observations': receipt}
            record_id = plan['id'] + '_montage'
            evidence_digest = _atomic_json(self.profile_path.parent / 'resource-profile-evidence' / f'{record_id}.json', evidence)
            document.setdefault('evidence', {})['montage'] = {'record_id': record_id, 'sha256': evidence_digest}
            _atomic_json(self.profile_path.parent / 'resource-profile-history' / f'{_digest(old)}.json', old)
            candidate_path = self.profile_path.with_name('.' + plan['id'] + '.candidate.json')
            try:
                _atomic_json(candidate_path, document)
                self._load(candidate_path)
                _atomic_json(self.profile_path, document)
            finally:
                candidate_path.unlink(missing_ok=True)
        return self._load()
