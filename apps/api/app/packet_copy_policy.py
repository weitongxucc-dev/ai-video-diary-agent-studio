"""One granted packet-copy unit, with stream costs and live file allocations.

The creation owner proves artifact lineage and authorization. This module proves
only the declared resource shape. It neither grants work nor certifies throughput.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import stat

import psutil

from app.device_resource_volumes import existing_volume
from app.creation.render_generation import render_resource_scope


PACKET_COPY_POLICY = 'packet-copy-bounded-preparation/v1'
PACKET_COPY_UNIT_PREFIX = 'render:packet-copy:v1:'
_ROLES = {'manifest', 'staging', 'published'}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _positive(value):
    return type(value) is int and value > 0


@dataclass(frozen=True)
class PacketCopyBudget:
    backend_identity: str
    max_process_rss_bytes: int
    additional_memory_bytes: int
    max_process_threads: int
    threads: int
    runtime_sample_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_manifest_bytes: int
    preparation_timeout_seconds: float
    policy_id: str = PACKET_COPY_POLICY

    def __post_init__(self):
        if (not _sha(self.backend_identity) or self.policy_id != PACKET_COPY_POLICY
                or any(not _positive(getattr(self, name)) for name in (
                    'max_process_rss_bytes', 'additional_memory_bytes', 'max_process_threads',
                    'threads', 'max_stdout_bytes', 'max_stderr_bytes', 'max_manifest_bytes'))
                or self.additional_memory_bytes < self.max_process_rss_bytes
                or self.max_process_threads < self.threads
                or self.max_manifest_bytes > self.max_process_rss_bytes
                or any(type(value) not in {int, float} or not math.isfinite(value) or value <= 0
                    for value in (self.runtime_sample_seconds, self.preparation_timeout_seconds))
                or self.runtime_sample_seconds > .25):
            raise ValueError('Packet copy needs a finite explicit process preparation policy')

    @property
    def digest(self):
        return _digest(asdict(self))


def packet_copy_preparation_budget(*, host_budget, backend_identity):
    """Explicit first-use choice from an existing CPU process policy, not its rate.

    Preserve its finite RSS/thread/time envelopes. The current native command
    adapter has 64 KiB stdout and 256 KiB stderr buffers; metadata receives at
    most 1/16 of the RSS envelope and 64 KiB per reduction unit. These versioned
    preparation allowances are not model/codec measurements or a film limit.
    """
    cpu = host_budget.cpu_render
    if cpu is None:
        raise ValueError('Packet-copy preparation needs an explicit host process policy')
    return PacketCopyBudget(backend_identity, cpu.additional_memory_bytes,
        cpu.additional_memory_bytes, cpu.max_process_threads, 1,
        host_budget.sample_seconds, 65536, 262144,
        min(65536, cpu.additional_memory_bytes // 16), cpu.max_timeout_seconds)


def _locations(write_paths, write_files, publication_mode):
    if (not isinstance(write_paths, dict) or not isinstance(write_files, dict)
            or set(write_paths) != _ROLES or set(write_files) != _ROLES
            or publication_mode not in {'atomic_rename', 'copy'}):
        raise ValueError('Packet-copy paths must name every actual newly written file role')
    paths, files, volumes = {}, {}, {}
    for role in sorted(_ROLES):
        directory, file = Path(write_paths[role]).expanduser(), Path(write_files[role]).expanduser()
        if (not directory.is_absolute() or not file.is_absolute() or file.is_symlink()
                or file.parent.resolve() != directory.resolve()):
            raise ValueError('Packet-copy file is not in its declared actual directory')
        paths[role], files[role] = str(directory.resolve()), str(file.parent.resolve() / file.name)
        volumes[role] = existing_volume(directory)[0]
    if len(set(files.values())) != len(files):
        raise ValueError('Packet-copy manifest, staging and publication files must be distinct')
    if publication_mode == 'atomic_rename' and volumes['staging'] != volumes['published']:
        raise ValueError('Atomic publication requires staging and destination on the same actual volume')
    return paths, files, volumes


def publication_target_fields(paths, files, volumes, published_target):
    """Bind the final name to the same controlled publication slot and volume."""
    if published_target is None:
        return {}
    target = Path(published_target).expanduser()
    directory = Path(paths['published'])
    if (not target.is_absolute() or target.name in {'.', '..'} or target.is_symlink() or target.parent.resolve() != directory
            or str(target.parent.resolve() / target.name) in files.values()
            or existing_volume(target.parent)[0] != volumes['published']):
        raise ValueError('Published target must be distinct and in its sealed publication directory and volume')
    facts = {'path': str(target.parent.resolve() / target.name), 'directory': str(directory),
             'volume_id': volumes['published']}
    return {'publication_target': facts, 'publication_target_digest': _digest(facts)}


def packet_copy_output_allocation(*, write_paths, write_files, publication_mode,
                                  manifest_bytes, reserve_bytes, published_target=None):
    """Finite available storage candidate, never an encoded-output prediction.

    Existing parents already occupy free space. Copy publication reserves two
    live outputs; a same-volume atomic rename reserves one. Allocate at most
    half the spendable space: filesystem metadata and other writers must not
    invalidate an otherwise unchanged unit as soon as it creates a file.
    This is a finite allocation policy, not a muxer size prediction. Caller
    still needs fresh host admission, including the original volume reserve.
    """
    if not _positive(manifest_bytes) or type(reserve_bytes) is not int or reserve_bytes < 0:
        raise ValueError('Packet-copy storage needs explicit manifest bytes and volume reserve')
    paths, files, volumes = _locations(write_paths, write_files, publication_mode)
    target = publication_target_fields(paths, files, volumes, published_target)
    if (any(Path(path).exists() for path in files.values())
            or target and Path(target['publication_target']['path']).exists()):
        raise ValueError('New packet-copy allocation cannot overwrite an existing or partial output')
    per_volume = {}
    for role, volume in volumes.items():
        item = per_volume.setdefault(volume, {'free': psutil.disk_usage(str(existing_volume(paths[role])[1])).free,
                                              'fixed': reserve_bytes, 'copies': 0})
        if role == 'manifest':
            item['fixed'] += manifest_bytes
        elif role == 'staging' or publication_mode == 'copy':
            item['copies'] += 1
    limits = []
    for item in per_volume.values():
        if type(item['free']) is not int:
            raise OSError('Packet-copy write volume available space is unknown')
        available = item['free'] - item['fixed']
        if available < 0:
            raise ValueError('Packet-copy write volume has insufficient or unknown available space')
        if item['copies']:
            limits.append(available // (2 * item['copies']))
    limit = min(limits)
    if limit <= 0:
        raise ValueError('Packet-copy write volumes cannot reserve an output')
    return limit


def packet_copy_resources(*, budget, manifest_digest, unit_id, parents, expected_total_packets,
                          write_paths, write_files, output_limit_bytes, manifest_bytes, publication_mode,
                          published_target=None, render_scope=None):
    """Build facts from all actual parents; source bytes are stream work, not RSS."""
    if not isinstance(budget, PacketCopyBudget) or not _sha(manifest_digest) or not _sha(unit_id):
        raise ValueError('Packet copy needs a policy and original manifest/unit digests')
    fields = {'artifact_id', 'sha256', 'size_bytes', 'packet_count', 'producer_request_id', 'producer_generation'}
    if not isinstance(parents, (tuple, list)) or not parents:
        raise ValueError('Packet copy needs all actual parents')
    for parent in parents:
        if (not isinstance(parent, dict) or set(parent) != fields or not _sha(parent['sha256'])
                or any(not isinstance(parent[name], str) or not parent[name].strip()
                       for name in ('artifact_id', 'producer_request_id'))
                or any(not _positive(parent[name]) for name in ('size_bytes', 'packet_count', 'producer_generation'))):
            raise ValueError('Packet-copy parent receipt facts are incomplete')
    # Keep order and repeated uses: a repeated parent is real input work too.
    parent_facts = [dict(parent) for parent in parents]
    if (not _positive(expected_total_packets)
            or sum(parent['packet_count'] for parent in parent_facts) != expected_total_packets
            or not _positive(output_limit_bytes) or not _positive(manifest_bytes)
            or manifest_bytes > budget.max_manifest_bytes
            or len(json.dumps(parent_facts, ensure_ascii=False).encode()) > budget.max_manifest_bytes):
        raise ValueError('Packet-copy packet clock or bounded manifest declaration is invalid')
    paths, files, volumes = _locations(write_paths, write_files, publication_mode)
    allocations = {'manifest': manifest_bytes, 'staging': output_limit_bytes,
                   'published': output_limit_bytes if publication_mode == 'copy' else 0}
    return {'kind': 'creation_packet_copy', 'execution_mode': 'ffmpeg_packet_copy',
        'read_mode': 'stream', 'role': 'picture_packet_copy', 'manifest_digest': manifest_digest,
        'unit_id': unit_id, 'parents': parent_facts, 'parent_bindings_digest': _digest(parent_facts),
        'input_count': len(parent_facts), 'source_bytes': sum(parent['size_bytes'] for parent in parent_facts),
        'expected_total_packets': expected_total_packets, 'budget_digest': budget.digest,
        'backend_identity': budget.backend_identity, 'threads': budget.threads,
        'max_output_bytes': output_limit_bytes, 'max_stdout_bytes': budget.max_stdout_bytes,
        'max_stderr_bytes': budget.max_stderr_bytes, 'manifest_bytes': manifest_bytes,
        'timeout_seconds': budget.preparation_timeout_seconds,
        'publication_mode': publication_mode, 'write_paths': paths, 'write_files': files,
        'write_allocations': allocations, 'write_volume_ids': volumes,
        **publication_target_fields(paths, files, volumes, published_target),
        **render_resource_scope(render_scope),
        'workload_provenance': {'mode': 'bounded_preparation', 'policy_id': budget.policy_id,
            'deadline_source': 'sealed_packet_copy_preparation_policy',
            'deadline_seconds': budget.preparation_timeout_seconds,
            'throughput_verification': 'unknown', 'decode_encode_work': 'not_applicable',
            'output_limit_basis': 'finite_storage_allocation_not_encoded_size_prediction'}}


def packet_copy_refusal(resources, budget):
    if budget is None:
        return 'packet_copy_policy_unavailable'
    if resources.get('budget_digest') != budget.digest or resources.get('backend_identity') != budget.backend_identity:
        return 'packet_copy_profile_changed'
    try:
        expected = packet_copy_resources(budget=budget, **{key: resources[key] for key in (
            'manifest_digest', 'unit_id', 'parents', 'expected_total_packets', 'write_paths', 'write_files',
            'manifest_bytes', 'publication_mode')},
            output_limit_bytes=resources['max_output_bytes'],
            published_target=resources['publication_target']['path'] if 'publication_target' in resources else None,
            render_scope={key:resources[key] for key in ('render_generation_id','render_input_timeline_version_id')}
                if 'render_generation_id' in resources else None)
        if json.dumps(resources, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
            return 'packet_copy_resources_invalid'
    except OSError:
        return 'packet_copy_filesystem_unavailable'
    except (ValueError, KeyError, TypeError):
        return 'packet_copy_resources_invalid'
    return None


def packet_copy_remaining_allocations(resources, *, running):
    """Reserve pending writes; only a sealed immutable target proves publication finished."""
    target = resources.get('publication_target')
    target_info = None
    if target is not None:
        bound = publication_target_fields(resources['write_paths'], resources['write_files'],
            resources['write_volume_ids'], target['path'])
        if (bound['publication_target'] != target
                or bound['publication_target_digest'] != resources.get('publication_target_digest')):
            raise ValueError('Published target no longer matches its sealed location')
        try:
            target_info = Path(target['path']).lstat()
        except FileNotFoundError:
            pass
    used, observed = {}, {}
    for role, path in resources['write_files'].items():
        try:
            info = Path(path).lstat()
        except FileNotFoundError:
            used[role] = 0
            continue
        observed[role] = info
    if target_info is not None:
        copying = observed.get('published')
        if copying is not None and (copying.st_dev, copying.st_ino, copying.st_size) != (
                target_info.st_dev, target_info.st_ino, target_info.st_size):
            raise ValueError('Publication temporary and final names are different live files')
        if target_info.st_dev != target['volume_id']:
            raise ValueError('Published target moved to another volume')
        # ArtifactStore links its exact temporary name to its final name, then unlinks the former.
        links = 2 if copying is not None else 1
        if (not stat.S_ISREG(target_info.st_mode) or target_info.st_size <= 0 or target_info.st_nlink != links
                or copying is not None and copying.st_nlink != links):
            raise ValueError('Published target is not the sealed regular file or exact publication link pair')
        observed['published'] = target_info
    for role, info in observed.items():
        links = 2 if role == 'published' and target_info is not None and info.st_nlink == 2 else 1
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != links:
            raise ValueError('Packet-copy output is not its own regular staging/publication file')
        used[role] = info.st_size
        cap = resources['manifest_bytes'] if role == 'manifest' else resources['max_output_bytes']
        if info.st_size > cap or not running:
            raise ValueError('Packet-copy output exceeds its allocation or already exists before admission')
    allocations = resources['write_allocations']
    if target_info is not None:
        # The owner publishes only after encoding and copy/fsync/hash/close finish.
        # Its original output caps stay sealed; neither output can grow after this boundary.
        return {'manifest': allocations['manifest'] - used['manifest'], 'staging': 0, 'published': 0}
    if resources['publication_mode'] == 'atomic_rename':
        if used['staging'] and used['published']:
            raise ValueError('Atomic publication unexpectedly has two live output copies')
        return {'manifest': allocations['manifest'] - used['manifest'],
                'staging': allocations['staging'] - max(used['staging'], used['published']), 'published': 0}
    return {role: allocations[role] - used[role] for role in allocations}
