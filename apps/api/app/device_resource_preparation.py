"""Prepare CPU policy through the existing device permit, then atomically publish it.

This owner measures synthetic CPU work only. The policy is a bounded engineering
starting point, not an estimate of model memory or a promise about a whole film.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

import psutil

from app.audio_timeline import PCM_CONTAINER_ALLOWANCE_BYTES
from app.cpu_preparation_video import preparation_video_command
from app.artwork_resource_preparation import (extend_render_budget, measure_template_inputs,
    measure_native_inputs, extend_native_budget)
from app.device_resource_configuration import backend_identity
from app.device_resource_profile import DeviceProfileIdentity, DeviceProfileUnavailable, _BUDGET_TYPES, _digest, load_device_resource_profile
from app.device_resource_volumes import sample_write_volumes
from app.device_scheduler import SubmissionIntent, SchedulePolicy, VerifiedWork
from app.device_runtime import OwnerRegistration
from app.host_memory import MemoryPolicy, sample_host_memory
from app.host_resource_probes import HostBudget, CpuRenderBudget, CpuAudioBudget, RuntimeCpuPolicy
from app.granted_subprocess import run_granted_command, GrantedCommandError
from app.visual_effects import VisualEffects


OWNER_TYPE = 'device_preparation'
POLICY_ID = 'interactive-cpu-preparation/v1'


class PreparationSamplingIncomplete(ValueError):
    """The original run did not establish a complete resource observation."""


@dataclass(frozen=True)
class CpuPreparationShape:
    width_px: int
    height_px: int
    frame_rate: int
    duration_seconds: float
    clip_count: int
    audio_input_count: int
    sample_rate: int
    channels: int
    source_bytes: int | None = None
    overlay_count: int = 0
    template_count: int = 0
    native_command: bool = False
    source_width_px: int | None = None
    source_height_px: int | None = None
    visual_effects: dict | None = None

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in (
                self.width_px, self.height_px, self.frame_rate, self.clip_count,
                self.audio_input_count, self.sample_rate, self.channels)):
            raise ValueError('CPU preparation shape needs positive integer dimensions/counts')
        if type(self.overlay_count) is not int or self.overlay_count < 0:
            raise ValueError('Overlay count must be a nonnegative integer')
        if type(self.template_count) is not int or not 0 <= self.template_count <= self.overlay_count:
            raise ValueError('HTML动画图层数必须属于总叠加图层数')
        if type(self.native_command) is not bool:
            raise ValueError('程序化执行准备需要明确的布尔选择')
        if self.source_bytes is not None and (type(self.source_bytes) is not int or self.source_bytes <= 0):
            raise ValueError('Known source bytes must be a positive exact file size')
        if (self.source_width_px, self.source_height_px) != (None, None) and any(
                type(value) is not int or value <= 0 or value % 2
                for value in (self.source_width_px, self.source_height_px)):
            raise ValueError('Source dimensions must be a complete even YUV width/height pair')
        if self.visual_effects is not None:
            effects = VisualEffects.model_validate(self.visual_effects)
            if effects.beauty_strength or effects.skin_exposure:
                raise ValueError('人像局部处理需要对应模型许可，不能用基础CPU准备代替')
        if (type(self.duration_seconds) not in (float, int) or not math.isfinite(self.duration_seconds)
                or self.duration_seconds <= 0 or self.width_px % 2 or self.height_px % 2):
            raise ValueError('CPU preparation needs finite duration and even YUV dimensions')


@dataclass(frozen=True)
class CpuPreparationPolicy:
    """Versioned, adjustable probe cost and sharing policy; no business size limit.

    One encoder thread and nice 10 yield CPU priority to interactive work.
    Available memory already excludes current resident usage. One host-process
    RSS worth of additional future growth is retained as recovery allowance;
    a probe can request half of the remaining currently available headroom.
    Twofold measured margins are declared engineering allowance, not measured
    capacity. Probe duration/timeout bound preparation cost, not the target film.
    """
    id: str = POLICY_ID
    recovery_rss_multiplier: float = 1.0
    probe_headroom_fraction: float = .5
    measured_margin: float = 2.0
    probe_seconds: float = 2.0
    timeout_seconds: float = 30.0
    sample_seconds: float = .1
    poll_seconds: float = 1.0
    cpu_nice: int = 10
    encoder: str = "libx264"
    encoder_preset: str = "fast"
    encoder_crf: int = 20
    probe_threads_per_input: int = 2
    probe_controller_threads: int = 4

    def __post_init__(self):
        if (self.id != POLICY_ID or not math.isfinite(self.recovery_rss_multiplier) or self.recovery_rss_multiplier <= 0
                or not 0 < self.probe_headroom_fraction < 1 or not math.isfinite(self.measured_margin) or self.measured_margin < 1
                or not all(math.isfinite(v) and v > 0 for v in
                    (self.probe_seconds, self.timeout_seconds, self.sample_seconds, self.poll_seconds))):
            raise ValueError('Invalid CPU preparation policy')


def _read(path):
    return json.loads(Path(path).read_bytes())


def _atomic_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def _profile_lock(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _profile_state(path):
    document = _read(path) if path.exists() else None
    return (document["revision"], _digest(document)) if document else (None, None)


def request_kind(*, database_path, request_id, actor):
    """Select the original preparation service; its status rechecks full identity."""
    with sqlite3.connect(Path(database_path).resolve().as_uri() + '?mode=ro', uri=True) as connection:
        connection.execute('PRAGMA query_only=ON')
        row = connection.execute('SELECT owner_type, workspace_id, member_id, unit_key '
            'FROM device_work_requests WHERE id=?', (request_id,)).fetchone()
    if row is None:
        raise KeyError(request_id)
    if row[:3] != (OWNER_TYPE, actor.workspace_id, actor.member_id):
        raise PermissionError('Preparation request belongs to another owner, workspace or member')
    kinds = {'cpu-prepare:v1': 'cpu', 'narration-prepare:v1': 'narration', 'montage-prepare:v1': 'montage'}
    if row[3] not in kinds:
        raise ValueError('Unknown device preparation execution unit')
    return kinds[row[3]]


class DeviceResourcePreparation:
    """One narrow device owner; caller supplies existing identity and scheduler.

    prepare() does bounded filesystem/host reads and records the server-owned
    plan. registration() belongs in the existing scheduler with bootstrap_budget;
    it does not start it. finalize() accepts a completed scheduler receipt, never
    caller-provided measurement facts. The main composition controls lifecycle.
    """
    def __init__(self, *, profile_path, device: DeviceProfileIdentity, settings, write_paths,
                 verify_identity, policy=CpuPreparationPolicy()):
        self.profile_path = Path(profile_path)
        if not self.profile_path.is_absolute():
            raise ValueError('Device resource profile path must be absolute')
        self.root = self.profile_path.parent / 'resource-preparations'
        self.device, self.settings = device, settings
        self.write_paths = {name: str(Path(path).resolve()) for name, path in write_paths.items()}
        if set(self.write_paths) != {'database', 'output', 'temporary'}:
            raise ValueError('Preparation needs actual database/output/temporary locations')
        self.verify_identity, self.policy = verify_identity, policy

    def _shape_payload(self, shape):
        values = asdict(shape)
        # Zero remains the original durable request shape.
        if shape.overlay_count == 0:
            values.pop('overlay_count')
        if shape.template_count == 0:
            values.pop('template_count')
        if not shape.native_command:
            values.pop('native_command')
        for key in ('source_width_px', 'source_height_px', 'visual_effects'):
            if values[key] is None:
                values.pop(key)
        return values

    def prepare(self, shape: CpuPreparationShape, actor, *, request_id: str):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise ValueError('Preparation needs a bounded stable request identity')
        if self.verify_identity(actor) != 'ready':
            raise PermissionError('Device preparation needs a current authorized member')
        identity = actor.identity_payload()
        key = {'workspace_id': actor.workspace_id, 'actor_identity': identity, 'request_id': request_id}
        plan_id = _digest(key)[:32]
        binding = {**key, 'shape': self._shape_payload(shape), 'device': asdict(self.device), 'write_paths': self.write_paths}
        path = self.root / 'plans' / f'{plan_id}.json'
        with _profile_lock(path):
            if path.exists():
                plan = self._plan(plan_id)
                if plan.get('request_binding') != binding:
                    raise ValueError('同一准备请求键的输入已变化；请读取原结果，不复用旧键改写请求。')
                return plan_id, self.bootstrap_budget(plan_id)
            return self._prepare_new(shape, actor, plan_id=plan_id, request_binding=binding)

    def _prepare_new(self, shape: CpuPreparationShape, actor, *, plan_id, request_binding):
        if self.verify_identity(actor) != 'ready':
            raise PermissionError('Device preparation needs a current authorized local member')
        if self.device.platform != 'darwin':
            raise DeviceProfileUnavailable('当前CPU准备策略支持macOS；其他平台需要对应系统压力适配。')
        with _profile_lock(self.profile_path):
            previous = _read(self.profile_path) if self.profile_path.exists() else None
            resolved = (load_device_resource_profile(self.profile_path, device=self.device,
                backend_identities=lambda name, operation: backend_identity(name, operation, settings=self.settings))
                if previous else None)
            expected_revision = previous['revision'] if previous else None
            expected_digest = _digest(previous) if previous else None
        # Preparation owns CPU operation budgets, not existing host-wide policy.
        # Missing fields receive the versioned baseline; declared values retain
        # their original meaning, including an explicit observation-only IO None.
        inherited = ({name: getattr(resolved.budget, name) for name, value in previous['budget'].items()
            if name not in _BUDGET_TYPES and (name != 'memory_policy' or value is not None)}
            if previous else {})
        ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
        if not ffmpeg or not ffprobe:
            raise DeviceProfileUnavailable('请先修复应用附带的 FFmpeg/FFprobe，再准备基础媒体能力。')
        memory = sample_host_memory()
        host_rss = psutil.Process().memory_info().rss
        # This is additional future-growth headroom, not a second subtraction of
        # the running host's occupancy (already reflected in available_bytes).
        reserve = math.ceil(host_rss * self.policy.recovery_rss_multiplier)
        if memory.pressure == 'unknown' or memory.available_bytes is None:
            raise DeviceProfileUnavailable('无法读取系统内存压力；请恢复系统资源观察后重新准备。')
        preparation_reserve = max(reserve, inherited.get('min_available_memory_bytes', 0))
        if memory.pressure == 'critical' or memory.available_bytes <= preparation_reserve:
            raise DeviceProfileUnavailable('当前内存压力过高；请等待或关闭占用较大的应用后重新准备。')
        memory_limit = math.floor((memory.available_bytes - preparation_reserve) * self.policy.probe_headroom_fraction)
        probe_seconds = min(shape.duration_seconds, self.policy.probe_seconds)
        if shape.clip_count > math.ceil(probe_seconds * shape.frame_rate):
            raise DeviceProfileUnavailable('本次单位包含的镜头多于有限小样本帧数；请拆小执行单元或选择成本更高的准备策略。')
        # Raw RGBA and float PCM bytes provide a declared finite output envelope.
        # Encoded output is still checked; this formula is not an encoder guarantee.
        raw_video = math.ceil(shape.width_px * shape.height_px * 4 * shape.frame_rate * probe_seconds)
        raw_audio = math.ceil(shape.sample_rate * shape.channels * 4 * probe_seconds)
        output_limit = raw_video + raw_audio
        cpu = RuntimeCpuPolicy(mode='darwin_nice', nice=self.policy.cpu_nice, evidence_reference=self.policy.id)
        render = CpuRenderBudget(probe_seconds, shape.width_px * shape.height_px * probe_seconds,
            shape.width_px, shape.height_px, output_limit, shape.clip_count, shape.overlay_count, output_limit, 1,
            self.policy.timeout_seconds, memory_limit, output_limit, max((psutil.cpu_count() or 1) + 2,
                max(shape.clip_count, shape.audio_input_count, shape.overlay_count) * self.policy.probe_threads_per_input
                + self.policy.probe_controller_threads), cpu)
        render = extend_render_budget(shape, render, output_limit)
        cadence = self.policy.poll_seconds + self.policy.sample_seconds
        budget = HostBudget(100, reserve, None, output_limit, None, output_limit,
            sample_seconds=self.policy.sample_seconds, cpu_render=render,
            memory_policy=MemoryPolicy('darwin', cadence * 3, cadence * 2, self.policy.id))
        try:
            budget = replace(budget, **inherited)
        except ValueError as exc:
            raise DeviceProfileUnavailable('现设备策略与CPU准备不兼容；请先调整设备策略后重新准备。') from exc
        disks = sample_write_volumes(self.write_paths,
            {'database': 0, 'output': output_limit, 'temporary': 2 * output_limit},
            reserve_bytes=budget.min_free_disk_bytes, verify_writable=True)
        if not all(item['passed'] for item in disks.values()):
            raise DeviceProfileUnavailable('实际输出卷或临时卷空间不足；请释放对应卷空间或更改该目录后重新准备。')
        identities = {name: backend_identity(name, render, settings=self.settings)
                      for name in ('cpu_render', 'cpu_audio', 'clip_preview')}
        if shape.native_command:
            from app.native_preparation_probe import require_native_dependencies
            require_native_dependencies(self.settings)
            identities['native_command'] = backend_identity('native_command', None, settings=self.settings)
        if not all(identities.values()):
            raise DeviceProfileUnavailable('基础媒体工具身份无法核验；请修复应用工具后重新准备。')
        plan = {'format_version': 1, 'id': plan_id, 'device': asdict(self.device),
            'request_binding': request_binding, 'actor_identity': actor.identity_payload(),
            'workspace_id': actor.workspace_id, 'member_id': actor.member_id,
            'shape': asdict(shape), 'policy': asdict(self.policy), 'bootstrap_budget': asdict(budget),
            'backends': identities, 'write_paths': self.write_paths, 'ffmpeg': ffmpeg, 'ffprobe': ffprobe,
            'host_observation': {'pressure': memory.pressure, 'available_bytes': memory.available_bytes,
                'host_rss_bytes': host_rss, 'recovery_allowance_bytes': reserve,
                'preparation_headroom_reserve_bytes': preparation_reserve,
                'scope': 'observation_at_prepare_not_future_capacity'},
            'host_policy_source': {'profile_revision': expected_revision, 'profile_digest': expected_digest,
                'evidence_reference': previous.get('evidence', {}).get('host') if previous else None,
                'preserved_fields': sorted(inherited),
                'new_fields_source': self.policy.id,
                'max_unit_input_bytes_scope': 'per_unit_input_capacity_not_cumulative_authorization'},
            'expected_revision': expected_revision, 'expected_profile_digest': expected_digest, 'probe_seconds': probe_seconds}
        plan['input_digest'] = _digest(plan)
        _atomic_json(self.root / 'plans' / f'{plan_id}.json', plan)
        return plan_id, budget

    def _plan(self, plan_id):
        if not isinstance(plan_id, str) or len(plan_id) != 32 or any(c not in '0123456789abcdef' for c in plan_id):
            raise ValueError('Invalid preparation identity')
        plan = _read(self.root / 'plans' / f'{plan_id}.json')
        if _digest({key: value for key, value in plan.items() if key != 'input_digest'}) != plan['input_digest']:
            raise ValueError('Preparation plan changed')
        return plan

    def intent(self, plan_id, actor):
        plan = self._plan(plan_id)
        if not self._can_access(plan, actor):
            raise PermissionError('Preparation belongs to another local member')
        return SubmissionIntent(plan['workspace_id'], OWNER_TYPE, plan_id, plan['input_digest'],
            'cpu-prepare:v1', SchedulePolicy())

    def registration(self):
        return OwnerRegistration(self, self)

    def _can_access(self, plan, actor):
        return (getattr(actor, 'workspace_id', None), getattr(actor, 'member_id', None)) == (
            plan['workspace_id'], plan['member_id']) and actor.identity_payload() == plan['actor_identity'] and self.verify_identity(actor) == 'ready'

    def _resources(self, plan):
        shape, limits = plan['shape'], plan['bootstrap_budget']['cpu_render']
        return {'kind': 'creation_render', 'execution_mode': 'cpu_ffmpeg', 'duration_seconds': plan['probe_seconds'],
            'width_px': shape['width_px'], 'height_px': shape['height_px'], 'input_bytes': 0,
            'clip_count': shape['clip_count'], 'overlay_count': shape.get('overlay_count', 0), 'max_output_bytes': limits['max_output_bytes'],
            'threads': limits['max_threads'], 'timeout_seconds': limits['max_timeout_seconds']}

    def verify_submission(self, intent, actor):
        plan = self._plan(intent.owner_id)
        if not self._can_access(plan, actor) or intent != self.intent(plan['id'], actor):
            raise PermissionError('Preparation submission is not the server-owned plan')
        return VerifiedWork(plan['workspace_id'], OWNER_TYPE, plan['id'], plan['input_digest'],
            'cpu-prepare:v1', plan['member_id'], 'normal_make', self._resources(plan))

    def check_current(self, request, conn):
        try:
            plan = self._plan(request.owner_id)
            if (plan['device'] != asdict(self.device) or plan['write_paths'] != self.write_paths
                    or _profile_state(self.profile_path) != (plan['expected_revision'], plan['expected_profile_digest'])
                    or (request.workspace_id, request.member_id, request.owner_type, request.unit_key) !=
                       (plan['workspace_id'], plan['member_id'], OWNER_TYPE, 'cpu-prepare:v1')
                    or request.input_digest != plan['input_digest'] or request.resources != self._resources(plan)):
                return 'stale'
            from app.creation_execution import CreationExecutionActor
            actor = CreationExecutionActor.from_event(plan['workspace_id'], plan['actor_identity'])
            if not self._can_access(plan, actor):
                return 'unauthorized'
            if any(backend_identity(name, None, settings=self.settings) != identity
                   for name, identity in plan['backends'].items()):
                return 'dependency'
            return 'ready'
        except (OSError, KeyError, ValueError):
            return 'dependency'

    def capture_local(self, request, conn):
        return lambda current: self.check_current(current, conn)

    def check_local(self, request, conn, observed):
        return observed(request)

    def can_manage(self, request, actor):
        return self._can_access(self._plan(request.owner_id), actor)

    can_view_detail = can_manage

    def can_wake(self, request, actor):
        return False

    def verify_retry(self, request, actor, reason, conn):
        raise PermissionError('请读取本次准备结果；失败后重新准备会保留原失败记录。')

    def bootstrap_budget(self, plan_id):
        from app.device_resource_profile import _budget
        data = dict(self._plan(plan_id)['bootstrap_budget'])
        data['cpu_render'] = _budget(CpuRenderBudget, data['cpu_render'])
        data['memory_policy'] = MemoryPolicy(**data['memory_policy'])
        return HostBudget(**data)

    def execute(self, grant, scheduler):
        plan = self._plan(grant.owner_id)
        shape = CpuPreparationShape(**plan['shape'])
        duration = plan['probe_seconds']
        started = time.monotonic()
        deadline = started + plan['policy']['timeout_seconds']
        observations = []
        native_receipt = None
        limit = plan['bootstrap_budget']['cpu_render']['max_output_bytes']
        def run(argv, paths=(), *, env=None):
            result = run_granted_command(scheduler, grant, argv,
                timeout_seconds=deadline - time.monotonic(), max_stdout_bytes=65536, max_stderr_bytes=65536,
                output_paths=paths, max_output_file_bytes=limit, env=env)
            if result.returncode:
                raise ValueError('CPU preparation command failed')
            observed = result.observed_resources
            if (observed is None or not observed.rss_sample_complete or not observed.threads_sample_complete
                    or observed.peak_rss_bytes is None or observed.peak_threads is None):
                raise PreparationSamplingIncomplete('CPU preparation resource observation is incomplete')
            observations.append(asdict(observed))
            return result
        try:
            with tempfile.TemporaryDirectory(prefix='cpu-preparation-', dir=self.write_paths['temporary']) as temporary:
                video, audio = Path(temporary) / 'sample.mp4', Path(temporary) / 'sample.wav'
                command, graphic_paths = preparation_video_command(plan, shape, video)
                measure_template_inputs(shape, video, duration, command, run)
                run(command, (video, *graphic_paths))
                command = [plan['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-y', '-filter_threads', '1', '-filter_complex_threads', '1']
                graph = ''.join(f'sine=frequency={440 + index}:sample_rate={shape.sample_rate}:'
                    f'duration={duration}[a{index}];' for index in range(shape.audio_input_count))
                graph += ''.join(f'[a{i}]' for i in range(shape.audio_input_count)) + f'amix=inputs={shape.audio_input_count}:normalize=0[a]'
                command += ['-filter_complex', graph, '-map', '[a]', '-t', str(duration), '-ar', str(shape.sample_rate),
                    '-ac', str(shape.channels), '-threads', '1', '-c:a', 'pcm_f32le', str(audio)]
                run(command, (video, audio))
                native_receipt = measure_native_inputs(shape, video, duration, self.settings, run)
                media = {}
                for name, path in (('video', video), ('audio', audio)):
                    result = run([plan['ffprobe'], '-v', 'error', '-show_entries',
                        'format=duration:stream=codec_type,width,height,sample_rate,channels', '-of', 'json', str(path)])
                    payload = json.loads(result.stdout)
                    actual_duration = float(payload['format']['duration'])
                    if not math.isfinite(actual_duration) or abs(actual_duration - duration) > 1 / shape.frame_rate:
                        raise ValueError('CPU preparation output duration is invalid')
                    stream = next(item for item in payload['streams'] if item['codec_type'] == name)
                    if name == 'video' and (stream['width'], stream['height']) != (shape.width_px, shape.height_px):
                        raise ValueError('CPU preparation output dimensions changed')
                    if name == 'audio' and (int(stream['sample_rate']), stream['channels']) != (shape.sample_rate, shape.channels):
                        raise ValueError('CPU preparation audio shape changed')
                    media[name] = {'bytes': path.stat().st_size, 'duration_seconds': actual_duration,
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                if any(backend_identity(name, None, settings=self.settings) != identity for name, identity in plan['backends'].items()):
                    raise ValueError('CPU preparation backend changed during measurement')
                receipt = {'format_version': 1, 'plan_id': plan['id'], 'request_id': grant.request_id,
                    'generation': grant.generation, 'input_digest': grant.input_digest,
                    'device': plan['device'], 'backends': plan['backends'], 'shape': plan['shape'],
                    'policy': plan['policy'], 'probe_seconds': duration, 'media': media,
                    'elapsed_seconds': time.monotonic() - started, 'observed_resources': observations}
                if native_receipt is not None:
                    receipt['native_command'] = native_receipt
                receipt_path = self.root / 'receipts' / f'{grant.request_id}-{grant.generation}.json'
                digest = _atomic_json(receipt_path, receipt)
            scheduler.complete_unit(grant, checkpoint_database_path=scheduler.database_path,
                checkpoint_write=lambda conn, current: 'device-resource-preparation:' + digest)
        except (GrantedCommandError, ValueError, OSError, KeyError, StopIteration) as exc:
            # The broker preserves unknown child release by raising RecoveryBlocked,
            # which is deliberately not caught here. No profile is published on failure.
            reason = ('device_preparation_sampling_incomplete' if isinstance(exc, PreparationSamplingIncomplete)
                else 'device_preparation_failed')
            scheduler.fail_unit(grant, reason=reason, failure_write=lambda conn, current: None)

    def _request(self, *, database_path, request_id, actor):
        with sqlite3.connect(Path(database_path).resolve().as_uri() + '?mode=ro', uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            row = connection.execute('SELECT * FROM device_work_requests WHERE id=?', (request_id,)).fetchone()
        if row is None:
            raise KeyError(request_id)
        if row['owner_type'] != OWNER_TYPE:
            raise PermissionError('Request is not a device preparation')
        plan = self._plan(row['owner_id'])
        if not self._can_access(plan, actor):
            raise PermissionError('Preparation result belongs to another member')
        return row, plan

    def status(self, *, database_path, request_id, actor):
        row, plan = self._request(database_path=database_path, request_id=request_id, actor=actor)
        result = {'id': row['id'], 'plan_id': plan['id'], 'state': row['state'], 'wait_reason': row['wait_reason']}
        if row['state'] == 'failed' and row['wait_reason'] == 'device_preparation_sampling_incomplete':
            result.update(failure_reason='本次准备未取得完整的资源采样，未启用新的资源档。',
                repair_action='先核验并收束原准备，确认资源释放后重新准备相同规格。原失败记录保留。')
        return result

    def status_for_plan(self, *, database_path, plan_id, actor):
        if not self._can_access(self._plan(plan_id), actor):
            raise PermissionError('Preparation belongs to another member')
        with sqlite3.connect(Path(database_path).resolve().as_uri() + '?mode=ro', uri=True) as connection:
            connection.execute('PRAGMA query_only=ON')
            row = connection.execute('SELECT id FROM device_work_requests WHERE workspace_id=? '
                'AND owner_type=? AND owner_id=?', (actor.workspace_id, OWNER_TYPE, plan_id)).fetchone()
        return None if row is None else self.status(database_path=database_path, request_id=row[0], actor=actor)

    def finalize(self, *, database_path, request_id, actor):
        row, plan = self._request(database_path=database_path, request_id=request_id, actor=actor)
        if row['state'] != 'done':
            raise DeviceProfileUnavailable('设备准备尚未取得完成回执；请读取原请求，不重复启动。')
        receipt_path = self.root / 'receipts' / f"{row['id']}-{row['last_grant_generation']}.json"
        raw = receipt_path.read_bytes()
        if row['checkpoint_ref'] != 'device-resource-preparation:' + hashlib.sha256(raw).hexdigest():
            raise DeviceProfileUnavailable('设备准备回执与许可检查点不匹配；保留原档供核查。')
        receipt = json.loads(raw)
        if ((row['workspace_id'], row['member_id'], row['unit_key']) != (plan['workspace_id'], plan['member_id'], 'cpu-prepare:v1')
                or receipt['request_id'] != row['id'] or receipt['generation'] != row['last_grant_generation']
                or receipt['input_digest'] != row['input_digest'] or row['input_digest'] != plan['input_digest']
                or receipt['plan_id'] != plan['id'] or receipt['device'] != asdict(self.device)
                or receipt['backends'] != plan['backends'] or receipt['shape'] != plan['shape']
                or json.loads(row['resources_json']) != self._resources(plan)):
            raise DeviceProfileUnavailable('设备准备来源或设备已变化；保留原回执并重新准备。')
        identities = {name: backend_identity(name, None, settings=self.settings) for name in plan['backends']}
        if identities != plan['backends']:
            raise DeviceProfileUnavailable('媒体工具已变化；需要重新准备对应能力，原记录保持。')
        return self._publish(plan, receipt, identities)

    def _publish(self, plan, receipt, identities):
        shape, policy = CpuPreparationShape(**plan['shape']), CpuPreparationPolicy(**plan['policy'])
        original = self.bootstrap_budget(plan['id'])
        samples = receipt['observed_resources']
        if not samples or any(not item['rss_sample_complete'] or not item['threads_sample_complete'] for item in samples):
            raise DeviceProfileUnavailable('完整进程资源采样缺失；不能把未知值当作零。')
        rss = min(original.cpu_render.additional_memory_bytes,
            math.ceil(max(item['peak_rss_bytes'] for item in samples) * policy.measured_margin))
        threads = min(original.cpu_render.max_process_threads,
            math.ceil(max(item['peak_threads'] for item in samples) * policy.measured_margin))
        video_bytes = math.ceil(shape.width_px * shape.height_px * 4 * shape.frame_rate * shape.duration_seconds)
        # The CPU delivery contract rounds picture to whole 30fps frames; its
        # PCM master is also a later mix input. Prepare both sides of that clock.
        delivery_duration = max(shape.duration_seconds, round(Fraction(str(shape.duration_seconds)) * 30) / 30)
        # Admission declares the mix container allowance, even when this short
        # measured WAV happened to need a smaller header.
        container_bytes = max(PCM_CONTAINER_ALLOWANCE_BYTES, receipt['media']['audio']['bytes'] - math.ceil(
            shape.sample_rate * shape.channels * 4 * plan['probe_seconds']))
        audio_bytes = math.ceil(shape.sample_rate * shape.channels * 4 * delivery_duration) + container_bytes
        input_bytes = max(video_bytes + audio_bytes * shape.audio_input_count, shape.source_bytes or 0)
        output_bytes = video_bytes + audio_bytes
        timeout = max(receipt['elapsed_seconds'], receipt['elapsed_seconds'] * shape.duration_seconds / plan['probe_seconds']) * policy.measured_margin
        render = replace(original.cpu_render, max_duration_seconds=delivery_duration,
            max_pixel_seconds=shape.width_px * shape.height_px * delivery_duration,
            max_input_bytes=input_bytes, max_output_bytes=output_bytes,
            max_timeout_seconds=timeout, additional_memory_bytes=rss, additional_disk_bytes=math.ceil(output_bytes * policy.measured_margin),
            max_process_threads=threads)
        if shape.template_count:
            raw_frames = math.ceil(shape.width_px*shape.height_px*4*30*delivery_duration)*shape.template_count
            render = replace(render, additional_disk_bytes=render.additional_disk_bytes+raw_frames)
        audio = CpuAudioBudget(shape.audio_input_count, max(audio_bytes * shape.audio_input_count, shape.source_bytes or 0), delivery_duration,
            delivery_duration, shape.sample_rate, shape.channels, audio_bytes, 1, timeout, rss, threads,
            policy.sample_seconds, rss, math.ceil(audio_bytes * policy.measured_margin))
        budget = replace(original, cpu_render=render, cpu_audio=audio,
            clip_preview=replace(render, max_pixel_seconds=render.max_pixel_seconds * 2),
            max_unit_input_bytes=max(original.max_unit_input_bytes, render.max_input_bytes))
        budget = extend_native_budget(shape, budget, receipt, plan, delivery_duration, original, policy)
        with _profile_lock(self.profile_path):
            old = _read(self.profile_path) if self.profile_path.exists() else None
            if old and old['profile_id'] == plan['id']:
                return load_device_resource_profile(self.profile_path, device=self.device, backend_identities=identities)
            if (old['revision'] if old else None, _digest(old) if old else None) != (plan['expected_revision'], plan['expected_profile_digest']):
                raise DeviceProfileUnavailable('设备档已由另一次准备更新；本次回执已保留，不能覆盖更新版本。')
            if old and old['device'] != asdict(self.device):
                raise DeviceProfileUnavailable('原设备档属于另一设备；不能覆盖。')
            document = deepcopy(old) if old else {'format_version': 1, 'device': asdict(self.device), 'backends': {}, 'evidence': {}, 'budget': {}}
            document.update(profile_id=plan['id'], revision=(old['revision'] + 1 if old else 1))
            owned = {'cpu_render', 'cpu_audio', 'clip_preview'}
            if shape.native_command:
                owned.add('native_command')
            prepared_budget = asdict(budget)
            # Only the prepared operation budgets and per-unit input capacity
            # change. Existing host policy is inherited before the probe itself;
            # no CPU sample calibrates unrelated declarations or host policy.
            for name, value in prepared_budget.items():
                if (name in owned or name == 'max_unit_input_bytes' or name not in document['budget']
                        or name == 'memory_policy' and document['budget'][name] is None):
                    document['budget'][name] = value
            document['backends'] = {**document.get('backends', {}), **identities}
            document['evidence'] = dict(document.get('evidence', {}))
            for name in ('host', *sorted(owned)):
                payload = ({key: value for key, value in document['budget'].items() if key not in _BUDGET_TYPES}
                           if name == 'host' else document['budget'][name])
                evidence = {'format_version': 1, 'device': asdict(self.device),
                    'backend_identity': None if name == 'host' else identities[name], 'budget_digest': _digest(payload),
                    'method': ('native-task-shell-preparation/v1' if name == 'native_command'
                        else policy.id + ':synthetic-granted-cpu'),
                    'input_range': {'requested_unit': plan['shape'], 'sample_seconds': plan['probe_seconds'],
                        'scope': 'synthetic_cpu_only', 'policy': plan['policy'], 'host_observation': plan['host_observation'],
                        'host_policy_source': plan.get('host_policy_source')},
                    'observations': {'request_id': receipt['request_id'], 'permit_generation': receipt['generation'],
                        'input_digest': receipt['input_digest'], 'media': receipt['media'],
                        'elapsed_seconds': receipt['elapsed_seconds'], 'process_samples': samples}}
                if name == 'native_command':
                    evidence['input_range']['scope'] = 'synthetic_native_task_shell'
                    evidence['observations']['native_command'] = receipt['native_command']
                record_id = f"{plan['id']}_{name}"
                digest = _atomic_json(self.profile_path.parent / 'resource-profile-evidence' / f'{record_id}.json', evidence)
                document['evidence'][name] = {'record_id': record_id, 'sha256': digest}
            if old:
                _atomic_json(self.profile_path.parent / 'resource-profile-history' / f'{_digest(old)}.json', old)
            # Validate the exact candidate and evidence before replacing the active file.
            candidate = self.profile_path.with_name('.' + plan['id'] + '.candidate.json')
            try:
                _atomic_json(candidate, document)
                load_device_resource_profile(candidate, device=self.device,
                    backend_identities=lambda name, operation: backend_identity(name, operation, settings=self.settings))
                os.replace(candidate, self.profile_path)
                directory = os.open(self.profile_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if candidate.exists():
                    candidate.unlink()
        return load_device_resource_profile(self.profile_path, device=self.device,
            backend_identities=lambda name, operation: backend_identity(name, operation, settings=self.settings))
