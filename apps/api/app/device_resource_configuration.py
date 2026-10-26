"""Compose device resource policy; execution and identity authorization live elsewhere."""
from __future__ import annotations

from dataclasses import asdict, replace
from functools import lru_cache
import hashlib
import json
import csv
from pathlib import Path
import platform
import shutil
import sys
from types import SimpleNamespace

import psutil

from app.device_resource_profile import (
    DeviceProfileIdentity, DeviceProfileUnavailable, ResolvedDeviceProfile,
    load_device_resource_profile, resolve_experimental_budget,
)


def _file_key(path):
    path = Path(path).resolve(strict=True)
    info = path.stat()
    return str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@lru_cache()
def _file_digest(key):
    with open(key[0], "rb") as source:
        value = hashlib.file_digest(source, "sha256").hexdigest()
    if _file_key(key[0]) != key:
        raise OSError("Backend changed during identity inspection")
    return value


def _hash_file(path):
    return _file_digest(_file_key(path))


def _identity(facts):
    return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def narration_runtime_identity_files(runtime_python):
    """Inspect an isolated runtime's installed files, without importing its code.

    Installer requirement labels are not a runtime identity. RECORD inventories
    and actual file stat identities detect package replacement without hashing
    native libraries or importing a model on a capability read.
    """
    root = Path(runtime_python).absolute().parent.parent
    config = root / 'pyvenv.cfg'
    lines = {key.strip().lower(): value.strip().lower()
             for line in config.read_text().splitlines() if '=' in line for key, value in [line.split('=', 1)]}
    if lines.get('include-system-site-packages') != 'false':
        raise ValueError('Narration preparation needs an isolated model runtime')
    sites = sorted(root.glob('lib/python*/site-packages'))
    if len(sites) != 1:
        raise ValueError('Narration runtime package directory is ambiguous')
    site = sites[0]
    records = sorted(site.glob('*.dist-info/RECORD'))
    required = {'mlx', 'mlx_audio', 'numpy'}
    packages = {record.parent.name.removesuffix('.dist-info').rsplit('-', 1)[0].replace('-', '_').lower() for record in records}
    if not required <= packages:
        raise ValueError('Narration runtime dependencies are not installed')
    identities = [_file_key(config)]
    # Bind installed import hooks too. External path roots are not in this
    # installation inventory and need their own runtime adapter.
    for path in site.glob('*.pth'):
        identities.append((_file_key(path), _hash_file(path)))
        for line in path.read_text().splitlines():
            if line.strip() and not line.startswith(('#', 'import ', 'import\t')):
                if not (site / line.strip()).resolve().is_relative_to(root.resolve()):
                    raise ValueError('Narration runtime has an external import path')
    for record in records:
        identities.append((_file_key(record), _hash_file(record)))
        with record.open(newline='') as stream:
            for item in csv.reader(stream):
                if not item or not item[0]:
                    raise ValueError('Invalid runtime package inventory')
                path = (site / item[0]).resolve(strict=True)
                if not path.is_relative_to(root.resolve()):
                    raise ValueError('Runtime package inventory leaves its installation')
                identities.append(_file_key(path))
    return identities


def backend_identity(name, budget, *, settings, model_catalog=None):
    """Identify installed adapters using cached file identities, never run them.

    Stat changes invalidate a cached digest. Model identity uses the existing
    manifest validator and worker/interpreter identities, not a weight scan.
    This is a configuration identity, not a performance measurement.
    """
    try:
        facts = {"platform": sys.platform, "os_release": platform.release(), "operation": name}
        if name in {"cpu_render", "presenter_cpu_render", "clip_preview", "cpu_audio", "cover", "scene_window"}:
            for tool in ("ffmpeg", "ffprobe"):
                path = shutil.which(tool)
                if path is None:
                    return None
                facts[tool] = _hash_file(path)
            if name in {"cpu_render", "presenter_cpu_render", "clip_preview"}:
                from app.creation.render_stage_use_case import CREATION_TASK_MONTAGE_RENDER_CONTRACT_VERSION
                facts["render_contract"] = CREATION_TASK_MONTAGE_RENDER_CONTRACT_VERSION
        elif name == "packet_copy":
            from app import montage, packet_copy_policy
            for tool in ("ffmpeg", "ffprobe"):
                path = shutil.which(tool)
                if path is None:
                    return None
                facts[tool] = _hash_file(path)
            facts["fragment_contract"] = montage.PICTURE_FRAGMENT_CONTRACT
            facts["implementation"] = _hash_file(montage.__file__)
            facts["resource_policy"] = _hash_file(packet_copy_policy.__file__)
        elif name == "quality":
            from app import quality_worker, quality_checks, quality_worker_runtime
            for tool in ("ffmpeg", "ffprobe"):
                actual = _hash_file(getattr(budget, tool + "_path"))
                if actual != getattr(budget, tool + "_sha256"):
                    return None
                facts[tool] = actual
            paths = ([Path(sys.executable)] if getattr(sys, "frozen", False) else
                     [Path(module.__file__) for module in (quality_checks, quality_worker_runtime, quality_worker)])
            worker_key = tuple(_file_key(path) for path in paths)
            actual_worker = _quality_worker_digest(worker_key)
            if actual_worker != budget.worker_sha256:
                return None
            facts["worker"] = actual_worker
        elif name in {"narration", "material_visual_query"}:
            if model_catalog is None:
                return None
            from app.model_runtime import QWEN3_TTS_06B_BASE_8BIT, CHINESE_CLIP_VIT_B16
            spec = QWEN3_TTS_06B_BASE_8BIT if name == "narration" else CHINESE_CLIP_VIT_B16
            status = model_catalog.status(spec)
            if not status.ready or not status.worker_path or not status.runtime_python_path:
                return None
            manifest = Path(status.model_path) / "manifest.json"
            if not manifest.is_file():
                return None  # An unrecorded model can be prepared, not called a matched profile.
            # Existing catalog validation checks paths/sizes, not content hashes.
            # Include file identities so ordinary model replacement invalidates the
            # profile, without re-reading weight bytes at every capability lookup.
            files = json.loads(manifest.read_bytes())["files"]
            facts["installed_files"] = [_file_key(Path(status.model_path) / item["path"]) for item in files]
            facts.update(model_id=spec.id, revision=spec.revision, manifest=_hash_file(manifest),
                         worker=_hash_file(status.worker_path), python=_hash_file(status.runtime_python_path))
            if name == 'narration':
                facts['text_embedding'] = _hash_file(Path(status.worker_path).with_name('qwen3_text_embedding.py'))
                facts['runtime_files'] = narration_runtime_identity_files(status.runtime_python_path)
        elif name == "media_script":
            # The current descriptor contains a declared stdlib/Pillow digest,
            # not a lifecycle-bound verification receipt. Do not call it matched.
            return None
        elif name == "observation":
            if not settings.native_ocr_worker_path:
                return None
            actual = _hash_file(settings.native_ocr_worker_path)
            if actual != budget.worker_sha256:
                return None
            facts["worker"] = actual
        elif name in {"native_command", "montage"}:
            source = ("creation/native_task_operations.py" if name == "native_command" else "montage.py")
            executable = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).parent / source
            facts["implementation"] = _hash_file(executable)
            if name == "native_command":
                facts["python"] = _hash_file(settings.model_runtime_python_path)
        else:
            return None  # No reliable installed identity adapter yet (for example lipsync).
        return _identity(facts)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _actual_narration_path(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise DeviceProfileUnavailable('配音执行器缺少明确的实际目录或工具位置')
    return Path(value).expanduser().resolve()


def narration_provider_binding(*, provider, status):
    """Bind invocation locations, including the venv launcher, without execution."""
    if not status.ready or not callable(getattr(provider, 'generate_granted', None)):
        raise DeviceProfileUnavailable('当前配音生成器没有可用的受许可模型执行入口')

    def invocation_path(name, value):
        path = _actual_narration_path(value)
        if name == 'runtime_python_path':
            # Two venv launchers may resolve to the same Python binary but use
            # different pyvenv.cfg/site-packages. Preserve the invoked launcher.
            configured = Path(value).expanduser()
            return configured.parent.resolve() / configured.name
        return path

    paths = {name: invocation_path(name, getattr(provider, name, None))
             for name in ('runtime_python_path', 'worker_path', 'model_path')}
    if any(path != invocation_path(name, getattr(status, name, None)) for name, path in paths.items()):
        raise DeviceProfileUnavailable('配音执行器与当前模型目录的工具或模型身份不一致')
    return paths


def narration_execution_facts(*, settings, model_catalog, generator, database_path, workspace_id, task_id):
    """Read the actual bound provider and its real write locations, without work.

    The generator owns task output layout. The model catalog owns installation
    identity. No budget value or provider class name proves either fact.
    """
    from app.model_runtime import QWEN3_TTS_06B_BASE_8BIT
    status = model_catalog.status(QWEN3_TTS_06B_BASE_8BIT)
    provider = getattr(generator, 'provider', None)
    paths = narration_provider_binding(provider=provider, status=status)
    # Bind identity to this exact catalog observation, not a second potentially
    # changed status lookup while constructing the same execution declaration.
    identity = backend_identity('narration', None, settings=settings,
        model_catalog=SimpleNamespace(status=lambda _spec: status))
    if identity is None:
        raise DeviceProfileUnavailable('配音模型、worker或隔离运行依赖身份无法核实，请重新准备对应能力')
    output_directory = getattr(generator, 'task_output_directory', None)
    if not callable(output_directory):
        raise DeviceProfileUnavailable('配音生成器尚未提供实际任务输出目录')
    write_paths = {'database': _actual_narration_path(database_path).parent,
        'temporary': _actual_narration_path(getattr(provider, 'temporary_root', None)),
        'model_cache': _actual_narration_path(getattr(provider, 'cache_root', None)),
        'output': _actual_narration_path(output_directory(workspace_id=workspace_id, task_id=task_id))}
    if paths != narration_provider_binding(provider=provider, status=status):
        raise DeviceProfileUnavailable('配音执行器在事实读取期间已更换，请重新读取')
    return {'backend_identity': identity,
            'write_paths': {name: str(path) for name, path in write_paths.items()}}


@lru_cache()
def _quality_worker_digest(keys):
    digest = hashlib.sha256()
    for key in keys:
        with open(key[0], "rb") as source:
            for chunk in iter(lambda: source.read(65536), b""):
                digest.update(chunk)
        if _file_key(key[0]) != key:
            raise OSError("Quality worker changed during identity inspection")
    return digest.hexdigest()


def _experiment(environment, experiment_id):
    from app.member_permits import (
        host_budget_from_env, memory_policy_from_env, cpu_render_budget_from_env,
        creation_stage_budgets_from_env, presenter_budgets_from_env, cpu_audio_budget_from_env,
        clip_preview_budget_from_env, media_script_budget_from_env, native_command_budget_from_env,
        observation_budget_from_env, quality_budget_from_env, cover_budget_from_env,
        material_visual_query_budget_from_env,
    )
    render = cpu_render_budget_from_env(environment) if any(
        key.startswith("DEVICE_CPU_RENDER_") and str(value).strip() for key, value in environment.items()) else None
    budget = host_budget_from_env(environment, cpu_render=render, memory_policy=memory_policy_from_env(environment),
                                  material_visual_query=material_visual_query_budget_from_env(environment))
    narration, montage = creation_stage_budgets_from_env(environment)
    presenter, presenter_cpu = presenter_budgets_from_env(environment)
    budget = replace(budget, narration=narration, montage=montage, presenter=presenter,
        presenter_cpu_render=presenter_cpu, cpu_audio=cpu_audio_budget_from_env(environment),
        clip_preview=clip_preview_budget_from_env(environment), media_script=media_script_budget_from_env(environment),
        native_command=native_command_budget_from_env(environment), observation=observation_budget_from_env(environment),
        quality=quality_budget_from_env(environment), cover=cover_budget_from_env(environment))
    return resolve_experimental_budget(budget, experiment_id=experiment_id)


def current_device_identity(environment) -> DeviceProfileIdentity:
    """Static identity shared by normal profile selection and preparation."""
    device_id = environment.get("DEVICE_ID", "").strip()
    if not device_id:
        raise DeviceProfileUnavailable("设备资源档缺少宿主设备身份；请通过桌面或已绑定设备配置启动。")
    return DeviceProfileIdentity(device_id, sys.platform, platform.machine(), psutil.virtual_memory().total)


def with_packet_copy_preparation(profile: ResolvedDeviceProfile, *, backend_identity: str | None) -> ResolvedDeviceProfile:
    """Explicitly select a finite first-use policy without rewriting the source file.

    Existing or invalidated explicit packet-copy configuration retains its own
    meaning. CPU process limits supply a candidate envelope, never a copy rate.
    """
    if (profile.source_kind != 'device_profile' or profile.budget.packet_copy is not None
            or 'packet_copy' in profile.unavailable):
        return profile
    if profile.budget.cpu_render is None:
        return replace(profile, unavailable={**profile.unavailable,
            'packet_copy': 'packet_copy_process_policy_unavailable'})
    if (not isinstance(backend_identity, str) or len(backend_identity) != 64
            or any(char not in '0123456789abcdef' for char in backend_identity)):
        return replace(profile, unavailable={**profile.unavailable,
            'packet_copy': 'packet_copy_backend_identity_unavailable'})
    from app.packet_copy_policy import packet_copy_preparation_budget
    try:
        candidate = packet_copy_preparation_budget(host_budget=profile.budget, backend_identity=backend_identity)
        budget = replace(profile.budget, packet_copy=candidate)
    except ValueError:
        return replace(profile, unavailable={**profile.unavailable,
            'packet_copy': 'packet_copy_preparation_policy_unavailable'})
    provenance = {'status': 'declared', 'verification_status': 'unknown',
        'selection': 'bounded_preparation', 'policy_id': candidate.policy_id,
        'source_profile_digest': profile.digest, 'source_profile_revision': profile.revision,
        'source_operation': 'cpu_render',
        'source_operation_budget_digest': _identity(asdict(profile.budget.cpu_render)),
        'backend_identity': backend_identity, 'budget_digest': candidate.digest,
        'throughput_verification': 'unknown'}
    digest = _identity({'selection_contract': 'device-profile-packet-copy-preparation/v1',
        'source_profile_digest': profile.digest, 'effective_budget': asdict(budget),
        'packet_copy_selection': provenance})
    # Source identity/revision/kind stay the on-disk profile's. The effective
    # digest separately identifies this composition and its explicit provenance.
    return replace(profile, budget=budget, digest=digest,
        evidence={**profile.evidence, 'packet_copy': provenance})


def resolve_device_resources(settings, environment, *, model_catalog=None, device=None,
                             backend_identities=None, select_packet_copy_preparation: bool = False) -> ResolvedDeviceProfile:
    """The only normal/experimental selection point; environment is explicit input."""
    if type(select_packet_copy_preparation) is not bool:
        raise DeviceProfileUnavailable("设备准备策略选择必须显式指定")
    mode = settings.device_resource_mode
    if mode == "experiment":
        experiment_id = settings.device_resource_experiment_id
        if not experiment_id or not experiment_id.strip():
            raise DeviceProfileUnavailable("实验资源配置缺少实验身份；请明确本次实验或使用正常设备档。")
        return _experiment(environment, experiment_id)
    if mode != "normal":
        raise DeviceProfileUnavailable("设备资源模式无效；请选择正常设备档或显式实验。")
    path = settings.device_resource_profile_path
    if not path:
        raise DeviceProfileUnavailable("设备资源档尚未准备；请从桌面设备配置准备，CLI需指定同一设备档路径。")
    if device is None:
        device = current_device_identity(environment)
    identities = backend_identities if backend_identities is not None else (
        lambda name, budget: backend_identity(name, budget, settings=settings, model_catalog=model_catalog))
    try:
        profile = load_device_resource_profile(path, device=device, backend_identities=identities)
        if (not select_packet_copy_preparation or profile.budget.packet_copy is not None
                or 'packet_copy' in profile.unavailable):
            return profile
        identity = None
        if profile.budget.cpu_render is not None:
            try:
                identity = identities('packet_copy', None) if callable(identities) else identities.get('packet_copy')
            except (OSError, ValueError, TypeError):
                # A copy-specific tool observation cannot disable unrelated reads
                # or expose raw filesystem/backend errors through configuration.
                identity = None
        return with_packet_copy_preparation(profile, backend_identity=identity)
    except DeviceProfileUnavailable as exc:
        raise DeviceProfileUnavailable(f"{exc}；请准备或更新该设备档，保留已有任务与产物。") from exc
