"""Prepare one real Qwen invocation using the existing device owner protocol.

CPU measurements never enter this model's receipt. Plans/receipts use the same
device preparation store and scheduler checkpoint mechanism; no model runner,
download queue, task artifact or cumulative authorization is created here.
"""
from dataclasses import asdict, dataclass, replace
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time

import psutil

from app.device_resource_preparation import (DeviceResourcePreparation, OWNER_TYPE,
    _atomic_json, _profile_lock, _profile_state, _read)
from app.device_resource_profile import (DeviceProfileUnavailable, _digest, _budget,
    host_budget_from_payload, load_device_resource_profile)
from app.device_resource_configuration import backend_identity, _hash_file, narration_provider_binding
from app.device_resource_volumes import narration_write_paths, sample_write_volumes
from app.device_scheduler import SubmissionIntent, SchedulePolicy, VerifiedWork
from app.host_resource_probes import NarrationBudget, RuntimeCpuPolicy, narration_scope_basis
from app.host_memory import sample_host_memory
from app.creation_execution import CreationExecutionActor, CreationStageGrantContext
from app.profile_reference_media import ProfileReferenceInput, materialize_reference
from app.profiles.contracts import profile_cache_identity, profile_use_failure
from app.model_runtime import QWEN3_TTS_06B_BASE_8BIT
from app.granted_subprocess import GrantedCommandError
from app.voice_clone import VoiceCloneError, text_to_speech_preflight
from app.source_policy import knowledge_item_readable as content_readable


UNIT_KEY = 'narration-prepare:v1'


@dataclass(frozen=True)
class NarrationPreparationShape:
    voice_profile_id: str
    voice_version: str
    text: str
    language: str
    output_duration_seconds: float
    pronunciation_references: tuple[dict[str, str], ...] = ()
    task_id: str | None = None

    def __post_init__(self):
        if (any(not isinstance(value, str) or not value.strip() for value in
                (self.voice_profile_id, self.voice_version, self.text))
                or self.language not in {'zh', 'en', 'zh-en'}
                or type(self.output_duration_seconds) not in {int, float}
                or not math.isfinite(self.output_duration_seconds) or self.output_duration_seconds <= 0):
            raise ValueError('模型准备需要明确声音版本、样本文案、语言及有限输出时长')
        if self.task_id is not None and (not isinstance(self.task_id, str) or not self.task_id.strip()):
            raise ValueError('词典任务范围需要明确的任务身份')
        if (not isinstance(self.pronunciation_references, (list, tuple))
                or any(not isinstance(entry, dict) or set(entry) != {'id', 'version'}
                    or any(not isinstance(value, str) or not value.strip() for value in entry.values())
                    for entry in self.pronunciation_references)):
            raise ValueError('发音词典需要明确的原条目ID和版本')
        entries = tuple(sorted((dict(entry) for entry in self.pronunciation_references),
                               key=lambda entry: (entry['id'], entry['version'])))
        if len({entry['id'] for entry in entries}) != len(entries):
            raise ValueError('同一词典条目不能重复或同时选择多个版本')
        object.__setattr__(self, 'pronunciation_references', entries)


@dataclass(frozen=True)
class NarrationPreparationPolicy:
    """Bound first-use cost, not model capacity or the total work size.

    Half currently available memory after the existing host reserve leaves room
    for unrelated growth. Twofold observed margins are engineering allowance.
    The PCM envelope permits stereo float samples up to 48 kHz, plus a finite
    response/header allowance; actual output is checked before publication.
    """
    id: str = 'qwen-single-call-preparation/v1'
    timeout_seconds: float = 120.0
    memory_fraction: float = .5
    measured_margin: float = 2.0
    threads: int = 1
    process_thread_multiplier: int = 2
    sample_seconds: float = .1
    output_sample_rate: int = 48000
    output_channels: int = 2
    output_sample_bytes: int = 4
    response_bytes: int = 65536

    def __post_init__(self):
        if (self.id != 'qwen-single-call-preparation/v1' or not 0 < self.memory_fraction < 1
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
                or not math.isfinite(self.measured_margin) or self.measured_margin < 1
                or not 0 < self.sample_seconds <= .25
                or any(type(value) is not int or value <= 0 for value in
                    (self.threads, self.process_thread_multiplier, self.output_sample_rate,
                     self.output_channels, self.output_sample_bytes, self.response_bytes))):
            raise ValueError('Invalid bounded model preparation policy')


def narration_preparation_shape_digest(*, voice_identity, reference, reference_sha256, reference_text, language,
                                       pronunciation_dictionary=()):
    """Normal consumers derive this from authorized inputs, never copy a budget ID."""
    return _digest({'voice': voice_identity, 'reference': reference,
        'reference_sha256': reference_sha256, 'language': language,
        'reference_text_sha256': hashlib.sha256(reference_text.encode()).hexdigest(),
        'pronunciation_dictionary': pronunciation_dictionary})


class _ObservationContext(CreationStageGrantContext):
    def __init__(self, *args, recheck, model_command, **kwargs):
        super().__init__(*args, **kwargs)
        self.observations, self.model_observations, self.recheck = [], [], recheck
        self.model_command = model_command

    def check(self):
        super().check()
        self.recheck()

    def run(self, *args, **kwargs):
        result = super().run(*args, **kwargs)
        observed = result.observed_resources
        if (observed is None or not observed.rss_sample_complete or not observed.threads_sample_complete
                or observed.peak_rss_bytes is None or observed.peak_threads is None):
            raise ValueError('模型准备进程采样不完整，不能将未知消耗当零')
        self.observations.append(asdict(observed))
        command = [str(value) for value in args[0]]
        if any(command[index:index+2] == list(self.model_command) for index in range(len(command)-1)):
            self.model_observations.append(asdict(observed))
        return result


class NarrationResourcePreparation(DeviceResourcePreparation):
    """Reuse device plan identity/status/recovery; override every CPU operation."""
    def __init__(self, *, repository, model_catalog, provider, policy=NarrationPreparationPolicy(), **kwargs):
        super().__init__(policy=policy, **kwargs)
        self.repository, self.model_catalog, self.provider = repository, model_catalog, provider
        self.root = self.profile_path.parent / 'resource-preparations' / 'narration'

    def prepare(self, shape, actor, *, request_id):
        if not isinstance(shape, NarrationPreparationShape):
            raise ValueError('Narration preparation requires its typed input')
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise ValueError('Preparation needs a bounded stable request identity')
        # Distinct model/CPU request spaces, while reusing the same scheduler owner.
        return super().prepare(shape, actor, request_id='narration:' + _digest(request_id))

    def _shape_payload(self, shape):
        payload = asdict(shape)
        # Preserve the persisted identity of existing empty-dictionary requests.
        if not payload['pronunciation_references']:
            payload.pop('pronunciation_references')
        else:
            payload['pronunciation_references'] = list(payload['pronunciation_references'])
        if payload['task_id'] is None:
            payload.pop('task_id')
        return payload

    def _identity(self):
        return backend_identity('narration', None, settings=self.settings, model_catalog=self.model_catalog)

    def _inputs(self, shape, actor):
        if self.verify_identity(actor) != 'ready':
            raise PermissionError('当前成员身份不可用')
        voice = self.repository.get_versioned_voice_profile(actor.workspace_id, shape.voice_profile_id, shape.voice_version)
        failure = profile_use_failure(voice, content_type='voice', usage_scope='narration')
        if (failure or voice.language not in {shape.language, 'zh-en'} or not voice.reference_text.strip()
                or (voice.workspace_id, voice.id, voice.version) != (actor.workspace_id, shape.voice_profile_id, shape.voice_version)):
            raise PermissionError('声音版本、授权或参考文字不可用于本次模型准备')
        if shape.task_id is not None:
            task = self.repository.get_creation_task(actor.workspace_id, shape.task_id)
            if not content_readable(user_id=actor.user_id or actor.member_id, local_diagnostic=False,
                                    visibility=task.visibility, owner_user_id=task.owner_user_id):
                raise PermissionError('当前成员不能使用该任务范围的发音词典')
        entries = []
        for selected in shape.pronunciation_references:
            entry = self.repository.get_pronunciation_entry(actor.workspace_id, selected['id'], selected['version'])
            if ((entry.workspace_id, entry.id, entry.version) !=
                    (actor.workspace_id, selected['id'], selected['version'])
                    or not entry.applies_to(workspace_id=actor.workspace_id,
                        person_profile_id=voice.person_profile_id, task_id=shape.task_id or '')
                    or (entry.scope == 'task' and shape.task_id is None)):
                raise PermissionError('发音词典原版本或适用范围不匹配')
            entries.append(entry.task_input())
        pronunciation_dictionary = tuple(sorted(entries,
            key=lambda entry: (entry['scope'], entry['id'], entry['version'])))
        preflight = text_to_speech_preflight(self.provider, language=shape.language,
            pronunciation_dictionary=pronunciation_dictionary)
        if preflight is not None:
            raise DeviceProfileUnavailable(preflight.repair_action)
        reference = self.repository.profile_reference_input(voice,
            viewer_user_id=actor.user_id or actor.member_id, local_diagnostic=False, for_generation=True)
        facts = {'voice': profile_cache_identity(voice), 'reference': asdict(reference),
            'reference_sha256': _hash_file(reference.source_path), 'reference_text': voice.reference_text,
            'language': shape.language, 'pronunciation_dictionary': list(pronunciation_dictionary)}
        facts['shape_digest'] = narration_preparation_shape_digest(voice_identity=facts['voice'],
            reference=facts['reference'], reference_sha256=facts['reference_sha256'],
            reference_text=voice.reference_text, language=shape.language,
            pronunciation_dictionary=pronunciation_dictionary)
        return voice, facts

    def _paths(self):
        status = self.model_catalog.status(QWEN3_TTS_06B_BASE_8BIT)
        if not status.ready:
            raise DeviceProfileUnavailable('请先通过现有模型安装入口安装或修复Qwen运行环境和权重')
        paths = narration_write_paths(database_path=self.repository.database_path,
            temporary_root=self.write_paths['temporary'], model_path=status.model_path)
        narration_provider_binding(provider=self.provider, status=status)
        if (getattr(self.provider, 'temporary_root', None) is None
                or Path(self.provider.temporary_root).resolve() != paths['temporary']
                or getattr(self.provider, 'cache_root', None) is None
                or Path(self.provider.cache_root).resolve() != paths['model_cache']):
            raise DeviceProfileUnavailable('声音执行器尚未绑定本次临时与模型缓存目录，请完成设备适配后准备')
        return {key: str(value) for key, value in paths.items()}

    def _prepare_new(self, shape, actor, *, plan_id, request_binding):
        identity = self._identity()
        if identity is None:
            raise DeviceProfileUnavailable('模型文件、worker或隔离运行依赖身份不完整，请修复模型安装后准备')
        voice, inputs = self._inputs(shape, actor)
        paths = self._paths()
        with _profile_lock(self.profile_path):
            current = load_device_resource_profile(self.profile_path, device=self.device,
                backend_identities=lambda name, operation: backend_identity(name, operation,
                    settings=self.settings, model_catalog=self.model_catalog))
            expected_revision, expected_digest = _profile_state(self.profile_path)
        memory = sample_host_memory()
        if (memory.available_bytes is None or memory.pressure not in {'normal', 'warning'}
                or memory.available_bytes <= current.budget.min_available_memory_bytes):
            raise DeviceProfileUnavailable('当前系统内存压力不可供有限模型准备；请等待压力恢复后重新准备')
        policy = self.policy
        rss = math.floor((memory.available_bytes-current.budget.min_available_memory_bytes) * policy.memory_fraction)
        output = math.ceil(shape.output_duration_seconds * policy.output_sample_rate
            * policy.output_channels * policy.output_sample_bytes) + policy.response_bytes
        reference_output = math.ceil((inputs['reference']['source_duration_seconds'] or shape.output_duration_seconds)
            * 16000 * 2) + policy.response_bytes
        preparation_output = output + reference_output
        limit = NarrationBudget(len(shape.text) + len(voice.reference_text), 1,
            inputs['reference']['size_bytes'], shape.output_duration_seconds, preparation_output, policy.threads,
            policy.timeout_seconds, rss, max(policy.threads, (psutil.cpu_count() or 1) * policy.process_thread_multiplier),
            policy.sample_seconds, rss, output + reference_output,
            RuntimeCpuPolicy(mode='darwin_background', evidence_reference=policy.id),
            input_scope='single_call_preparation_v1', preparation_backend_identity=identity,
            preparation_shape_digest=inputs['shape_digest'])
        budget = replace(current.budget, narration=limit)
        reservations = {'database': 0, 'temporary': preparation_output + limit.additional_disk_bytes,
                        'model_cache': limit.additional_disk_bytes}
        disks = sample_write_volumes(paths, reservations, reserve_bytes=budget.min_free_disk_bytes, verify_writable=True)
        if not all(item['passed'] for item in disks.values()):
            raise DeviceProfileUnavailable('模型缓存卷或实际临时卷空间不足，请释放对应卷后准备')
        plan = {'format_version': 1, 'id': plan_id, 'kind': 'narration', 'device': asdict(self.device),
            'request_binding': request_binding, 'actor_identity': actor.identity_payload(),
            'workspace_id': actor.workspace_id, 'member_id': actor.member_id, 'shape': self._shape_payload(shape),
            'policy': asdict(policy), 'bootstrap_budget': asdict(budget), 'inputs': inputs,
            'backends': {'narration': identity}, 'write_paths': self.write_paths,
            'actual_write_paths': paths, 'write_reservations': reservations,
            'host_observation': {'pressure': memory.pressure, 'available_bytes': memory.available_bytes,
                'scope': 'bounded_preparation_candidate_not_measured_capacity'},
            'model_output_limit': output,
            'expected_revision': expected_revision, 'expected_profile_digest': expected_digest}
        plan['input_digest'] = _digest(plan)
        _atomic_json(self.root / 'plans' / f'{plan_id}.json', plan)
        return plan_id, budget

    def bootstrap_budget(self, plan_id):
        return host_budget_from_payload(self._plan(plan_id)['bootstrap_budget'])

    def intent(self, plan_id, actor):
        plan = self._plan(plan_id)
        if not self._can_access(plan, actor):
            raise PermissionError('模型准备属于其他成员')
        return SubmissionIntent(plan['workspace_id'], OWNER_TYPE, plan_id, plan['input_digest'], UNIT_KEY, SchedulePolicy())

    def _resources(self, plan):
        budget = self.bootstrap_budget(plan['id']).narration
        return {'kind': 'creation_narration', 'execution_mode': 'isolated_qwen3_tts',
            'input_scope': budget.input_scope, 'budget_basis': narration_scope_basis(budget.input_scope),
            'text_chars': budget.max_text_chars, 'section_count': 1, 'reference_bytes': budget.max_reference_bytes,
            'duration_seconds': budget.max_duration_seconds, 'max_output_bytes': budget.max_output_bytes,
            'threads': budget.max_threads, 'timeout_seconds': budget.max_timeout_seconds,
            'preparation_backend_identity': budget.preparation_backend_identity,
            'preparation_shape_digest': budget.preparation_shape_digest,
            'write_volumes': {'paths': plan['actual_write_paths'], 'reservations': plan['write_reservations']}}

    def verify_submission(self, intent, actor):
        if intent != self.intent(intent.owner_id, actor):
            raise PermissionError('模型准备提交不匹配原计划')
        plan = self._plan(intent.owner_id)
        return VerifiedWork(plan['workspace_id'], OWNER_TYPE, plan['id'], plan['input_digest'], UNIT_KEY,
            plan['member_id'], 'normal_make', self._resources(plan))

    def _recheck(self, plan, actor):
        if not self._can_access(plan, actor):
            raise PermissionError('模型准备授权已变化')
        if (plan['device'] != asdict(self.device) or self._identity() != plan['backends']['narration']
                or self._paths() != plan['actual_write_paths']
                or self._inputs(NarrationPreparationShape(**plan['shape']), actor)[1] != plan['inputs']):
            raise ValueError('模型准备的声音、模型依赖或实际写入位置已变化')

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
        except (OSError, ValueError, KeyError, DeviceProfileUnavailable):
            return 'dependency'

    def execute(self, grant, scheduler):
        plan = self._plan(grant.owner_id)
        actor = CreationExecutionActor.from_event(plan['workspace_id'], plan['actor_identity'])
        started = time.monotonic()
        budget = self.bootstrap_budget(plan['id']).narration
        context = _ObservationContext(scheduler, grant, started + budget.max_timeout_seconds,
            budget.max_output_bytes, budget.max_threads, recheck=lambda: self._recheck(plan, actor),
            model_command=(str(self.provider.runtime_python_path), str(self.provider.worker_path)))
        try:
            context.check()
            with tempfile.TemporaryDirectory(prefix='narration-preparation-', dir=plan['actual_write_paths']['temporary']) as work:
                source = ProfileReferenceInput(**plan['inputs']['reference'])
                reference = materialize_reference(source, output_root=Path(work), grant_context=context,
                    recheck=lambda: context.check())
                result = self.provider.generate_granted(text=plan['shape']['text'],
                    reference_audio_path=reference.path, reference_text=plan['inputs']['reference_text'],
                    language=plan['shape']['language'],
                    pronunciation_dictionary=tuple(plan['inputs']['pronunciation_dictionary']),
                    output_path=Path(work) / 'sample.wav', grant_context=context)
                context.check()
                output = Path(result.audio_path)
                if (output.resolve() != (Path(work) / 'sample.wav').resolve() or not context.model_observations
                        or output.stat().st_size > plan['model_output_limit']
                        or not 0 < result.duration_seconds <= budget.max_duration_seconds):
                    raise ValueError('模型准备输出超出本次候选范围或缺少实际采样')
                receipt = {'format_version': 1, 'plan_id': plan['id'], 'request_id': grant.request_id,
                    'generation': grant.generation, 'input_digest': grant.input_digest, 'device': plan['device'],
                    'backends': plan['backends'], 'shape': plan['shape'], 'inputs': plan['inputs'],
                    'policy': plan['policy'], 'elapsed_seconds': time.monotonic() - started,
                    'observed_resources': context.observations,
                    'model_observed_resources': context.model_observations,
                    'media': {'sha256': _hash_file(output), 'bytes': output.stat().st_size,
                        'duration_seconds': result.duration_seconds, 'sample_rate': result.sample_rate},
                    'mlx_memory': {'state': 'unknown'}}
                digest = _atomic_json(self.root / 'receipts' / f'{grant.request_id}-{grant.generation}.json', receipt)
            scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                checkpoint_write=lambda conn, current: 'narration-resource-preparation:' + digest)
        except (GrantedCommandError, VoiceCloneError, OSError, ValueError, KeyError):
            scheduler.fail_unit(grant, reason='narration_preparation_failed', failure_write=lambda conn, current: None)

    def finalize(self, *, database_path, request_id, actor):
        row, plan = self._request(database_path=database_path, request_id=request_id, actor=actor)
        if row['state'] != 'done':
            raise DeviceProfileUnavailable('模型准备尚无完成回执，请读取原请求')
        raw = (self.root / 'receipts' / f"{row['id']}-{row['last_grant_generation']}.json").read_bytes()
        receipt_digest = hashlib.sha256(raw).hexdigest()
        receipt = json.loads(raw)
        if (row['checkpoint_ref'] != 'narration-resource-preparation:' + receipt_digest
                or (row['workspace_id'], row['member_id']) != (plan['workspace_id'], plan['member_id'])
                or (row['unit_key'], row['input_digest']) != (UNIT_KEY, plan['input_digest'])
                or json.loads(row['resources_json']) != self._resources(plan)
                or any(receipt[key] != plan[key] for key in ('device', 'backends', 'shape', 'inputs', 'input_digest'))
                or (receipt['plan_id'], receipt['request_id'], receipt['generation'])
                   != (plan['id'], row['id'], row['last_grant_generation'])):
            raise DeviceProfileUnavailable('模型准备回执与原许可、声音或模型身份不匹配')
        self._recheck(plan, actor)
        observations = receipt['model_observed_resources']
        if not observations or any(not item['rss_sample_complete'] or not item['threads_sample_complete']
                or type(item['peak_rss_bytes']) is not int or type(item['peak_threads']) is not int for item in observations):
            raise DeviceProfileUnavailable('模型准备采样不完整')
        original = self.bootstrap_budget(plan['id']).narration
        margin = plan['policy']['measured_margin']
        rss = min(original.max_process_rss_bytes, math.ceil(max(item['peak_rss_bytes'] for item in observations) * margin))
        prepared = replace(original, input_scope='single_call_prepared_v1',
            max_output_bytes=plan['model_output_limit'],
            preparation_receipt_sha256=receipt_digest, max_process_rss_bytes=rss, additional_memory_bytes=rss,
            max_process_threads=min(original.max_process_threads, math.ceil(max(item['peak_threads'] for item in observations) * margin)),
            max_timeout_seconds=min(original.max_timeout_seconds, max(receipt['elapsed_seconds'] * margin, self.policy.sample_seconds)))
        with _profile_lock(self.profile_path):
            document = _read(self.profile_path)
            if document['profile_id'] == plan['id']:
                return self._load()
            if (document['revision'], _digest(document)) != (plan['expected_revision'], plan['expected_profile_digest']):
                raise DeviceProfileUnavailable('设备档已更新，原模型准备回执保留，不能覆盖新版本')
            old = deepcopy(document)
            document.update(profile_id=plan['id'], revision=document['revision'] + 1)
            document['budget']['narration'] = asdict(prepared)
            document['backends']['narration'] = plan['backends']['narration']
            evidence = {'format_version': 1, 'device': plan['device'], 'backend_identity': plan['backends']['narration'],
                'budget_digest': _digest(asdict(prepared)), 'method': plan['policy']['id'],
                'input_range': {'shape': plan['shape'], 'shape_digest': plan['inputs']['shape_digest'],
                    'policy': plan['policy'], 'scope': 'one_recorded_model_invocation'},
                'observations': receipt}
            record_id = plan['id'] + '_narration'
            evidence_digest = _atomic_json(self.profile_path.parent / 'resource-profile-evidence' / f'{record_id}.json', evidence)
            document.setdefault('evidence', {})['narration'] = {'record_id': record_id, 'sha256': evidence_digest}
            _atomic_json(self.profile_path.parent / 'resource-profile-history' / f'{_digest(old)}.json', old)
            # Existing profile parser validates exact budget + receipt binding.
            candidate = self.profile_path.with_name('.' + plan['id'] + '.candidate.json')
            try:
                _atomic_json(candidate, document)
                self._load(candidate)
                _atomic_json(self.profile_path, document)
            finally:
                candidate.unlink(missing_ok=True)
        return self._load()

    def _load(self, path=None):
        return load_device_resource_profile(path or self.profile_path, device=self.device,
            backend_identities=lambda name, operation: backend_identity(name, operation,
                settings=self.settings, model_catalog=self.model_catalog))
