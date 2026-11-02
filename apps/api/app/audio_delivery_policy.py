"""Finite AAC encoding and two-stream mux units, independent of film size.

The creation owner binds parents to artifacts, authorization and original grants.
This module validates resource declarations, never scans parents or grants work.
"""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

from app.packet_copy_policy import (
    _digest, _sha, _positive, _locations,
)


AUDIO_ENCODE_POLICY = 'master-aac-first-use/v1'
FINAL_MUX_POLICY = 'picture-audio-mux-first-use/v1'
AUDIO_ENCODE_UNIT_PREFIX = 'render:audio-encode:v1:'
FINAL_MUX_UNIT_PREFIX = 'render:final-mux:v1:'
DELIVERY_ADOPTION_UNIT_PREFIX = 'render:delivery-adopt:v1:'
DELIVERY_KINDS = {'creation_audio_encode': ('audio_encode', AUDIO_ENCODE_UNIT_PREFIX),
                  'creation_final_mux': ('final_mux', FINAL_MUX_UNIT_PREFIX),
                  'creation_delivery_adoption': ('final_mux', DELIVERY_ADOPTION_UNIT_PREFIX)}


@dataclass(frozen=True)
class _DeliveryBudget:
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

    def __post_init__(self):
        expected = AUDIO_ENCODE_POLICY if type(self) is AudioEncodeBudget else FINAL_MUX_POLICY
        if (type(self) not in {AudioEncodeBudget, FinalMuxBudget} or self.policy_id != expected
                or not _sha(self.backend_identity)
                or any(not _positive(getattr(self, name)) for name in (
                    'max_process_rss_bytes', 'additional_memory_bytes', 'max_process_threads',
                    'threads', 'max_stdout_bytes', 'max_stderr_bytes', 'max_manifest_bytes'))
                or self.additional_memory_bytes < self.max_process_rss_bytes
                or self.max_process_threads < self.threads or self.max_manifest_bytes > self.max_process_rss_bytes
                or any(type(value) not in {int, float} or not math.isfinite(value) or value <= 0
                       for value in (self.runtime_sample_seconds, self.preparation_timeout_seconds))
                or self.runtime_sample_seconds > .25):
            raise ValueError('Audio delivery needs a finite explicit process policy')

    @property
    def digest(self):
        return _digest(asdict(self))


@dataclass(frozen=True)
class AudioEncodeBudget(_DeliveryBudget):
    policy_id: str = AUDIO_ENCODE_POLICY


@dataclass(frozen=True)
class FinalMuxBudget(_DeliveryBudget):
    policy_id: str = FINAL_MUX_POLICY


def audio_delivery_preparation_budget(*, operation, host_budget, backend_identity):
    """Select an existing process envelope explicitly, without borrowing a codec rate.

    Buffer allowances share the existing native media command's bounded buffers;
    metadata uses at most the packet-copy policy's bounded manifest allowance.
    These are process allowances, not measured AAC/mux capacity or film limits.
    """
    kind = {'audio_encode': AudioEncodeBudget, 'final_mux': FinalMuxBudget}.get(operation)
    if kind is None:
        raise ValueError('Unsupported audio delivery operation')
    source = audio_delivery_source_operation(operation=operation, host_budget=host_budget)
    cpu = getattr(host_budget, source) if source is not None else None
    if cpu is None:
        raise ValueError('Audio delivery needs an existing CPU process envelope')
    rss = cpu.max_process_rss_bytes if source == 'cpu_audio' else cpu.additional_memory_bytes
    return kind(backend_identity, rss, cpu.additional_memory_bytes, cpu.max_process_threads,
        1, host_budget.sample_seconds, 65536, 262144, min(65536, rss // 16), cpu.max_timeout_seconds)


def audio_delivery_source_operation(*, operation, host_budget):
    if operation not in {'audio_encode', 'final_mux'}:
        raise ValueError('Unsupported audio delivery operation')
    preferred = ('cpu_audio', 'cpu_render') if operation == 'audio_encode' else ('cpu_render', 'cpu_audio')
    return next((name for name in preferred if getattr(host_budget, name) is not None), None)


def _parent(value):
    fields = {'artifact_id', 'sha256', 'size_bytes', 'producer_request_id', 'producer_generation',
              'transform_id', 'transform_digest'}
    if (not isinstance(value, dict) or set(value) != {'binding', 'clock'}
            or not isinstance(value['binding'], dict) or set(value['binding']) != fields):
        raise ValueError('Delivery parent needs its original artifact and transform receipt')
    binding = value['binding']
    if (not _sha(binding['sha256']) or not _sha(binding['transform_digest'])
            or any(not isinstance(binding[key], str) or not binding[key].strip()
                   for key in ('artifact_id', 'producer_request_id', 'transform_id'))
            or not _positive(binding['size_bytes']) or not _positive(binding['producer_generation'])):
        raise ValueError('Delivery parent receipt is incomplete')


def _clock(value, *, encoded=False, picture=False):
    samples = {'sample_count', 'sample_rate', 'channels'}
    packets = {'packet_count', 'time_base_num', 'time_base_den', 'first_pts', 'last_end_pts'}
    fields = packets if picture else samples | (packets | {'initial_skip_samples', 'trailing_padding_samples'} if encoded else {'sample_format'})
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Delivery parent clock fields are incomplete')
    for key in fields - {'first_pts', 'last_end_pts', 'sample_format', 'initial_skip_samples', 'trailing_padding_samples'}:
        if not _positive(value[key]):
            raise ValueError('Delivery clock needs positive integer counts and rational time base')
    if picture or encoded:
        if (type(value['first_pts']) is not int or type(value['last_end_pts']) is not int
                or value['last_end_pts'] <= value['first_pts']):
            raise ValueError('Delivery packet interval is invalid')
    if encoded:
        for key in ('initial_skip_samples', 'trailing_padding_samples'):
            if value[key] is not None and (type(value[key]) is not int or value[key] < 0):
                raise ValueError('Unknown codec padding must remain unknown, not an invented count')
    elif not picture and value['sample_format'] not in {'s16le', 'f32le'}:
        raise ValueError('Master must identify its actual PCM format')


def _base(*, budget, manifest_digest, unit_id, parents, write_paths, write_files,
          output_limit_bytes, manifest_bytes, publication_mode):
    if (not _sha(manifest_digest) or not _sha(unit_id) or not _positive(output_limit_bytes)
            or not _positive(manifest_bytes) or manifest_bytes > budget.max_manifest_bytes):
        raise ValueError('Delivery needs finite storage and a bounded original manifest')
    for parent in parents:
        _parent(parent)
    if len(json.dumps(parents, ensure_ascii=False, allow_nan=False).encode()) > budget.max_manifest_bytes:
        raise ValueError('Delivery parent metadata exceeds the process manifest allowance')
    paths, files, volumes = _locations(write_paths, write_files, publication_mode)
    # Snapshot caller dictionaries: the sealed resource facts must not mutate later.
    parents = json.loads(json.dumps(parents, allow_nan=False))
    return {'read_mode': 'stream', 'manifest_digest': manifest_digest, 'unit_id': unit_id,
        'parents': parents, 'parent_bindings_digest': _digest(parents), 'input_count': len(parents),
        'source_bytes': sum(parent['binding']['size_bytes'] for parent in parents),
        'budget_digest': budget.digest, 'backend_identity': budget.backend_identity,
        'threads': budget.threads, 'max_output_bytes': output_limit_bytes,
        'max_stdout_bytes': budget.max_stdout_bytes, 'max_stderr_bytes': budget.max_stderr_bytes,
        'manifest_bytes': manifest_bytes, 'timeout_seconds': budget.preparation_timeout_seconds,
        'publication_mode': publication_mode, 'write_paths': paths, 'write_files': files,
        'write_volume_ids': volumes, 'write_allocations': {'manifest': manifest_bytes,
            'staging': output_limit_bytes, 'published': output_limit_bytes if publication_mode == 'copy' else 0},
        'workload_provenance': {'mode': 'bounded_first_use', 'policy_id': budget.policy_id,
            'deadline_source': 'sealed_delivery_process_policy',
            'deadline_seconds': budget.preparation_timeout_seconds, 'throughput_verification': 'unknown',
            'output_limit_basis': 'finite_storage_allocation_not_encoded_size_prediction'}}


def audio_encode_resources(*, budget, manifest_digest, unit_id, master, audio_recipe,
                          write_paths, write_files, output_limit_bytes, manifest_bytes, publication_mode):
    if type(budget) is not AudioEncodeBudget:
        raise ValueError('AAC encoding needs its own process policy')
    _parent(master)
    _clock(master['clock'])
    if (not isinstance(audio_recipe, dict) or set(audio_recipe) != {'codec', 'sample_rate', 'channels', 'parameters_digest', 'fragmented'}
            or audio_recipe['codec'] != 'aac' or not _sha(audio_recipe['parameters_digest'])
            or type(audio_recipe['fragmented']) is not bool
            or any(type(audio_recipe[key]) is not int or audio_recipe[key] != master['clock'][key]
                   for key in ('sample_rate', 'channels'))):
        raise ValueError('AAC recipe must bind the actual master format and sealed encoder parameters')
    result = _base(budget=budget, manifest_digest=manifest_digest, unit_id=unit_id, parents=[master],
        write_paths=write_paths, write_files=write_files, output_limit_bytes=output_limit_bytes,
        manifest_bytes=manifest_bytes, publication_mode=publication_mode)
    return {**result, 'kind': 'creation_audio_encode', 'execution_mode': 'ffmpeg_aac_encode',
        'role': 'master_audio_encode', 'audio_recipe': dict(audio_recipe)}


def final_mux_resources(*, budget, manifest_digest, unit_id, picture, encoded_audio, mux_recipe,
                       write_paths, write_files, output_limit_bytes, manifest_bytes, publication_mode):
    if type(budget) is not FinalMuxBudget:
        raise ValueError('Final mux needs its own process policy')
    for parent in (picture, encoded_audio):
        _parent(parent)
    _clock(picture['clock'], picture=True)
    _clock(encoded_audio['clock'], encoded=True)
    if (not isinstance(mux_recipe, dict) or set(mux_recipe) != {'video', 'audio', 'parameters_digest', 'fragmented', 'faststart'}
            or mux_recipe['video'] != 'copy' or mux_recipe['audio'] != 'copy'
            or not _sha(mux_recipe['parameters_digest'])
            or type(mux_recipe['fragmented']) is not bool or type(mux_recipe['faststart']) is not bool
            or mux_recipe['fragmented'] and mux_recipe['faststart']):
        raise ValueError('Final mux must copy both streams with compatible sealed MP4 parameters')
    result = _base(budget=budget, manifest_digest=manifest_digest, unit_id=unit_id, parents=[picture, encoded_audio],
        write_paths=write_paths, write_files=write_files, output_limit_bytes=output_limit_bytes,
        manifest_bytes=manifest_bytes, publication_mode=publication_mode)
    return {**result, 'kind': 'creation_final_mux', 'execution_mode': 'ffmpeg_stream_mux',
        'role': 'picture_audio_final_mux', 'mux_recipe': dict(mux_recipe)}


def delivery_adoption_resources(*, budget, manifest_digest, unit_id, predecessor,
                                video, subtitle, database_path, metadata_bytes):
    """Declare verification of existing files and the bounded adoption write set.

    The owner measures and binds serialized database metadata before admission.
    That reservation is separate from generated media bytes, which remain zero.
    It measures serialized metadata, not SQLite page or journal amplification;
    the existing disk floor and transactional failure handling still apply.
    """
    from app.device_resource_volumes import existing_volume
    if (type(budget) is not FinalMuxBudget or not _sha(manifest_digest) or not _sha(unit_id)
            or not _positive(metadata_bytes) or metadata_bytes > budget.max_process_rss_bytes):
        raise ValueError('Delivery adoption needs a finite metadata write set and process policy')
    if (not isinstance(predecessor, dict)
            or set(predecessor) != {'request_id', 'generation', 'receipt_id', 'resources_digest'}
            or any(not isinstance(predecessor[key], str) or not predecessor[key].strip()
                   for key in ('request_id', 'receipt_id'))
            or not _positive(predecessor['generation']) or not _sha(predecessor['resources_digest'])):
        raise ValueError('Delivery adoption needs the original failed request and settled receipt')
    inputs = [video] + ([] if subtitle is None else [subtitle])
    binding_fields = {'artifact_id', 'sha256', 'size_bytes', 'producer_request_id',
                      'producer_generation', 'transform_id', 'transform_digest'}
    bindings = []
    for index, facts in enumerate(inputs):
        fields = {'path', 'logical_ref', 'file_identity'} | ({'parent'} if index == 0 else binding_fields)
        if (not isinstance(facts, dict) or set(facts) != fields
                or any(not isinstance(facts[key], str) or not facts[key].strip() for key in ('path', 'logical_ref'))
                or not Path(facts['path']).is_absolute()):
            raise ValueError('Delivery adoption needs bound existing media paths')
        parent = facts['parent'] if index == 0 else {'binding': {key: facts[key] for key in binding_fields}, 'clock': {}}
        _parent(parent)
        bindings.append(parent['binding'])
        identity = facts['file_identity']
        if (not isinstance(identity, dict)
                or set(identity) != {'device', 'inode', 'size_bytes', 'mtime_ns', 'ctime_ns'}
                or any(type(value) is not int for value in identity.values())
                or identity['size_bytes'] != parent['binding']['size_bytes']):
            raise ValueError('Delivery adoption file identity does not match its original artifact')
    clock = video['parent']['clock']
    if not isinstance(clock, dict) or set(clock) != {'picture', 'audio'}:
        raise ValueError('Delivery adoption needs both original output clocks')
    _clock(clock['picture'], picture=True)
    _clock(clock['audio'], encoded=True)
    # The immediate failed predecessor may itself be an adoption attempt.
    # The owner verifies that chain back to the original video's producer.
    database = Path(database_path).expanduser().resolve()
    volume, _ = existing_volume(database.parent)
    sealed = json.loads(json.dumps({'predecessor': predecessor, 'video': video, 'subtitle': subtitle},
                                  ensure_ascii=False, allow_nan=False))
    manifest_bytes = len(json.dumps(sealed, ensure_ascii=False, sort_keys=True, allow_nan=False).encode())
    if manifest_bytes > budget.max_manifest_bytes:
        raise ValueError('Delivery adoption references exceed the finite manifest allowance')
    return {'kind': 'creation_delivery_adoption', 'execution_mode': 'verify_and_adopt',
        'role': 'final_delivery_adoption', 'read_mode': 'stream',
        'manifest_digest': manifest_digest, 'unit_id': unit_id, **sealed,
        'parent_bindings_digest': _digest(sealed), 'input_count': len(inputs),
        'source_bytes': sum(binding['size_bytes'] for binding in bindings),
        'budget_digest': budget.digest, 'backend_identity': budget.backend_identity,
        'threads': budget.threads, 'timeout_seconds': budget.preparation_timeout_seconds,
        'max_stdout_bytes': budget.max_stdout_bytes, 'max_stderr_bytes': budget.max_stderr_bytes,
        'manifest_bytes': manifest_bytes, 'metadata_bytes': metadata_bytes,
        'max_output_bytes': 0, 'generated_bytes_limit': 0, 'database_path': str(database),
        'write_paths': {'database': str(database.parent)}, 'write_volume_ids': {'database': volume},
        'write_allocations': {'database': metadata_bytes},
        'workload_provenance': {'mode': 'original_delivery_checkpoint_recovery',
            'policy_id': budget.policy_id, 'deadline_source': 'sealed_delivery_process_policy',
            'throughput_verification': 'unknown',
            'database_reservation_basis': 'serialized_adoption_write_set'}}


def audio_delivery_refusal(resources, budget):
    if budget is None:
        return 'audio_delivery_policy_unavailable'
    if resources.get('budget_digest') != budget.digest or resources.get('backend_identity') != budget.backend_identity:
        return 'audio_delivery_profile_changed'
    try:
        if resources.get('kind') == 'creation_delivery_adoption':
            expected = delivery_adoption_resources(budget=budget, **{key: resources[key] for key in (
                'manifest_digest', 'unit_id', 'predecessor', 'video', 'subtitle', 'database_path', 'metadata_bytes')})
        else:
            common = {key: resources[key] for key in ('manifest_digest', 'unit_id', 'write_paths', 'write_files',
                'manifest_bytes', 'publication_mode')}
            common.update(budget=budget, output_limit_bytes=resources['max_output_bytes'])
            if resources.get('kind') == 'creation_audio_encode' and len(resources['parents']) == 1:
                expected = audio_encode_resources(**common, master=resources['parents'][0], audio_recipe=resources['audio_recipe'])
            elif resources.get('kind') == 'creation_final_mux' and len(resources['parents']) == 2:
                expected = final_mux_resources(**common, picture=resources['parents'][0],
                    encoded_audio=resources['parents'][1], mux_recipe=resources['mux_recipe'])
            else:
                return 'audio_delivery_resources_invalid'
        if json.dumps(resources, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
            return 'audio_delivery_resources_invalid'
    except OSError:
        return 'audio_delivery_filesystem_unavailable'
    except (KeyError, ValueError, TypeError):
        return 'audio_delivery_resources_invalid'
    return None
